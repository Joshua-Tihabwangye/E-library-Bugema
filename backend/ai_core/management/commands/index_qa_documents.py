from django.core.management.base import BaseCommand, CommandError

from ai_core.document_ingestion import DocumentIndexingError, index_book_document
from catalog.models import Book


class Command(BaseCommand):
    help = "Index PDF book documents into the Q&A PGVector collection."

    def add_arguments(self, parser):
        parser.add_argument(
            "--book-id",
            type=int,
            help="Index a single book by ID.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Index all published PDF books.",
        )

    def handle(self, *args, **options):
        book_id = options.get("book_id")
        index_all = options.get("all")

        if not book_id and not index_all:
            raise CommandError("Provide either --book-id <id> or --all.")

        if book_id:
            books = Book.objects.filter(pk=book_id, is_published=True).select_related("author")
        else:
            books = Book.objects.filter(is_published=True, file_type="PDF").select_related("author")

        total = books.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No matching books found for indexing."))
            return

        self.stdout.write(f"Starting Q&A indexing for {total} book(s)...")
        indexed = 0
        failed = 0

        for position, book in enumerate(books, start=1):
            self.stdout.write(f"[{position}/{total}] Indexing book {book.id}: {book.title}")
            try:
                index_record = index_book_document(book)
                indexed += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Indexed {index_record.page_count} pages into "
                        f"{index_record.chunk_count} chunks."
                    )
                )
            except DocumentIndexingError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  ✗ {exc}"))
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  ✗ Unexpected failure: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Q&A indexing complete: {indexed} succeeded, {failed} failed."
            )
        )
