from django.db import models
from django.utils import timezone


class BookDocumentIndex(models.Model):
    """
    Tracks the state of Q&A document indexing for each book.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    book = models.OneToOneField(
        "catalog.Book",
        on_delete=models.CASCADE,
        related_name="document_index",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    source_url = models.URLField(max_length=500, blank=True, null=True)
    vector_ids = models.JSONField(default=list, blank=True)
    chunk_count = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, null=True)
    indexed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Book Document Index"
        verbose_name_plural = "Book Document Indexes"

    def __str__(self):
        return f"Document index for book {self.book_id} ({self.status})"

    def mark_success(
        self,
        *,
        source_url: str,
        vector_ids: list[str],
        chunk_count: int,
        page_count: int,
    ) -> None:
        self.status = self.Status.SUCCESS
        self.source_url = source_url
        self.vector_ids = vector_ids
        self.chunk_count = chunk_count
        self.page_count = page_count
        self.last_error = None
        self.indexed_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "source_url",
                "vector_ids",
                "chunk_count",
                "page_count",
                "last_error",
                "indexed_at",
                "updated_at",
            ]
        )

    def mark_failed(self, error_message: str) -> None:
        self.status = self.Status.FAILED
        self.last_error = error_message
        self.save(update_fields=["status", "last_error", "updated_at"])

    def reset_to_pending(self) -> None:
        self.status = self.Status.PENDING
        self.vector_ids = []
        self.chunk_count = 0
        self.page_count = 0
        self.last_error = None
        self.indexed_at = None
        self.save(
            update_fields=[
                "status",
                "vector_ids",
                "chunk_count",
                "page_count",
                "last_error",
                "indexed_at",
                "updated_at",
            ]
        )
