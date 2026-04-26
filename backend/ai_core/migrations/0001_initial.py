from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("catalog", "0007_book_visual_description"),
    ]

    operations = [
        migrations.CreateModel(
            name="BookDocumentIndex",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("success", "Success"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("source_url", models.URLField(blank=True, max_length=500, null=True)),
                ("vector_ids", models.JSONField(blank=True, default=list)),
                ("chunk_count", models.PositiveIntegerField(default=0)),
                ("page_count", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True, null=True)),
                ("indexed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "book",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_index",
                        to="catalog.book",
                    ),
                ),
            ],
            options={
                "verbose_name": "Book Document Index",
                "verbose_name_plural": "Book Document Indexes",
                "ordering": ["-updated_at"],
            },
        ),
    ]
