from django.db import transaction
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from catalog.models import Book

from .models import BookDocumentIndex
from .tasks import queue_delete_book_vectors, queue_index_book


TRACKED_FIELDS = (
    "title",
    "description",
    "author_id",
    "isbn",
    "file_type",
    "is_published",
    "file",
    "file_url",
)


def _snapshot(book: Book) -> dict:
    file_name = ""
    if getattr(book, "file", None):
        file_name = getattr(book.file, "name", "") or ""

    return {
        "title": book.title,
        "description": book.description,
        "author_id": book.author_id,
        "isbn": book.isbn,
        "file_type": book.file_type,
        "is_published": bool(book.is_published),
        "file": file_name,
        "file_url": book.file_url or "",
    }


def _should_index(book: Book) -> bool:
    has_source = bool(getattr(book, "file", None)) or bool(book.file_url)
    return bool(book.is_published and book.file_type == "PDF" and has_source)


@receiver(pre_save, sender=Book)
def capture_previous_book_state(sender, instance: Book, **kwargs):
    if not instance.pk:
        instance._previous_snapshot = None
        instance._previous_vector_ids = []
        return

    previous = (
        Book.objects.filter(pk=instance.pk)
        .values(
            "title",
            "description",
            "author_id",
            "isbn",
            "file_type",
            "is_published",
            "file",
            "file_url",
        )
        .first()
    )
    instance._previous_snapshot = previous
    previous_index = BookDocumentIndex.objects.filter(book_id=instance.pk).only("vector_ids").first()
    instance._previous_vector_ids = list((previous_index.vector_ids if previous_index else []) or [])


@receiver(post_save, sender=Book)
def auto_manage_book_indexing(sender, instance: Book, created: bool, **kwargs):
    previous = getattr(instance, "_previous_snapshot", None)
    previous_vector_ids = list(getattr(instance, "_previous_vector_ids", []) or [])

    if created:
        if _should_index(instance):
            transaction.on_commit(lambda: queue_index_book(instance.id))
        return

    current = _snapshot(instance)
    changed = previous is None or any(previous.get(field) != current[field] for field in TRACKED_FIELDS)
    if not changed:
        return

    has_existing_index = BookDocumentIndex.objects.filter(book_id=instance.id).exists()
    had_source = bool((previous or {}).get("file")) or bool((previous or {}).get("file_url"))
    has_source_now = bool(current.get("file")) or bool(current.get("file_url"))
    is_file_changed = previous is not None and previous.get("file") != current.get("file")
    is_file_url_changed = previous is not None and previous.get("file_url") != current.get("file_url")
    is_unpublished = previous is not None and previous.get("is_published") and not current.get("is_published")
    is_file_removed = previous is not None and had_source and not has_source_now

    requires_cleanup = bool(
        has_existing_index
        and (
            is_unpublished
            or is_file_removed
            or is_file_changed
            or is_file_url_changed
            or not _should_index(instance)
        )
    )
    if requires_cleanup:
        transaction.on_commit(
            lambda: queue_delete_book_vectors(instance.id, vector_ids=previous_vector_ids)
        )

    if _should_index(instance):
        transaction.on_commit(lambda: queue_index_book(instance.id))


@receiver(pre_delete, sender=Book)
def auto_cleanup_book_vectors(sender, instance: Book, **kwargs):
    previous_index = BookDocumentIndex.objects.filter(book_id=instance.pk).only("vector_ids").first()
    vector_ids = list((previous_index.vector_ids if previous_index else []) or [])
    queue_delete_book_vectors(instance.id, vector_ids=vector_ids)
