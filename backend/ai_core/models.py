from django.db import models


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
