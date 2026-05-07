from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from langchain_core.documents import Document
from rest_framework.test import APITestCase

from ai_core import ai_service
from ai_core.document_ingestion import (
    DocumentIndexingError,
    delete_book_vectors,
    index_book_document,
)
from ai_core.models import BookDocumentIndex
from catalog.models import Author, Book


User = get_user_model()


class BaseBookTestCase(TestCase):
    def setUp(self):
        self.author = Author.objects.create(name="Test Author")

    def create_book(
        self,
        *,
        isbn: str,
        title: str = "Test Book",
        is_published: bool = True,
        file_type: str = "PDF",
        file_url: str | None = None,
    ) -> Book:
        return Book.objects.create(
            title=title,
            author=self.author,
            description="A book description.",
            isbn=isbn,
            file_type=file_type,
            is_published=is_published,
            file_url=file_url,
            language="en",
        )


class DocumentIngestionTests(BaseBookTestCase):
    @patch("ai_core.document_ingestion.get_vector_store")
    @patch("ai_core.document_ingestion._split_documents")
    @patch("ai_core.document_ingestion._load_pdf_pages")
    @patch("ai_core.document_ingestion._download_pdf")
    def test_ingestion_success_updates_index_record(
        self,
        mock_download_pdf,
        mock_load_pdf_pages,
        mock_split_documents,
        mock_get_vector_store,
    ):
        book = self.create_book(isbn="9780000000001")
        mock_download_pdf.return_value = ("/tmp/fake.pdf", "https://cdn.example/book.pdf")
        page_docs = [
            Document(page_content="Page 1", metadata={"page": 1}),
            Document(page_content="Page 2", metadata={"page": 2}),
        ]
        chunk_docs = [
            Document(page_content="Chunk 1", metadata={"page": 1}),
            Document(page_content="Chunk 2", metadata={"page": 2}),
        ]
        mock_load_pdf_pages.return_value = page_docs
        mock_split_documents.return_value = chunk_docs
        vector_store = MagicMock()
        mock_get_vector_store.return_value = vector_store

        index_record = index_book_document(book)

        self.assertEqual(index_record.status, BookDocumentIndex.Status.SUCCESS)
        self.assertEqual(index_record.page_count, 2)
        self.assertEqual(index_record.chunk_count, 2)
        self.assertEqual(index_record.source_url, "https://cdn.example/book.pdf")
        self.assertEqual(
            index_record.vector_ids,
            [
                f"book-{book.id}-page-1-chunk-1",
                f"book-{book.id}-page-2-chunk-1",
            ],
        )
        vector_store.add_documents.assert_called_once()

    @patch("ai_core.document_ingestion._download_pdf")
    def test_ingestion_failure_marks_failed(self, mock_download_pdf):
        book = self.create_book(isbn="9780000000002")
        mock_download_pdf.side_effect = DocumentIndexingError("download failed")

        with self.assertRaises(DocumentIndexingError):
            index_book_document(book)

        index_record = BookDocumentIndex.objects.get(book=book)
        self.assertEqual(index_record.status, BookDocumentIndex.Status.FAILED)
        self.assertIn("download failed", index_record.last_error)

    @patch("ai_core.document_ingestion.get_vector_store")
    @patch("ai_core.document_ingestion._split_documents")
    @patch("ai_core.document_ingestion._load_pdf_pages")
    @patch("ai_core.document_ingestion._download_pdf")
    def test_reindex_replaces_old_vectors(
        self,
        mock_download_pdf,
        mock_load_pdf_pages,
        mock_split_documents,
        mock_get_vector_store,
    ):
        book = self.create_book(isbn="9780000000003")
        BookDocumentIndex.objects.create(
            book=book,
            status=BookDocumentIndex.Status.SUCCESS,
            vector_ids=["old-vec-1", "old-vec-2"],
            chunk_count=2,
            page_count=1,
            source_url="https://old.example/book.pdf",
        )
        mock_download_pdf.return_value = ("/tmp/fake.pdf", "https://new.example/book.pdf")
        mock_load_pdf_pages.return_value = [Document(page_content="Page 1", metadata={"page": 1})]
        mock_split_documents.return_value = [Document(page_content="New Chunk", metadata={"page": 1})]
        vector_store = MagicMock()
        mock_get_vector_store.return_value = vector_store

        index_record = index_book_document(book)

        vector_store.delete.assert_called_once_with(ids=["old-vec-1", "old-vec-2"])
        vector_store.add_documents.assert_called_once()
        self.assertEqual(index_record.vector_ids, [f"book-{book.id}-page-1-chunk-1"])
        self.assertEqual(index_record.source_url, "https://new.example/book.pdf")

    @patch("ai_core.document_ingestion.get_vector_store")
    def test_delete_vectors_resets_index_state(self, mock_get_vector_store):
        book = self.create_book(isbn="9780000000004")
        index_record = BookDocumentIndex.objects.create(
            book=book,
            status=BookDocumentIndex.Status.SUCCESS,
            vector_ids=["vec-1"],
            chunk_count=4,
            page_count=2,
        )
        vector_store = MagicMock()
        mock_get_vector_store.return_value = vector_store

        deleted = delete_book_vectors(book.id)
        index_record.refresh_from_db()

        self.assertTrue(deleted)
        vector_store.delete.assert_called_once_with(ids=["vec-1"])
        self.assertEqual(index_record.status, BookDocumentIndex.Status.PENDING)
        self.assertEqual(index_record.vector_ids, [])
        self.assertEqual(index_record.chunk_count, 0)
        self.assertEqual(index_record.page_count, 0)


class CleanupSignalTests(BaseBookTestCase):
    def test_unpublish_queues_cleanup(self):
        with patch("ai_core.signals.queue_index_book"):
            book = self.create_book(
                isbn="9780000000011",
                is_published=True,
                file_url="https://cdn.example/book.pdf",
            )

        BookDocumentIndex.objects.create(
            book=book,
            status=BookDocumentIndex.Status.SUCCESS,
            vector_ids=["vec-a", "vec-b"],
        )

        with patch("ai_core.signals.queue_delete_book_vectors") as mock_delete, patch(
            "ai_core.signals.queue_index_book"
        ) as mock_index:
            with self.captureOnCommitCallbacks(execute=True):
                book.is_published = False
                book.save()

        mock_delete.assert_called_once_with(book.id, vector_ids=["vec-a", "vec-b"])
        mock_index.assert_not_called()

    def test_file_url_change_queues_cleanup_and_reindex(self):
        with patch("ai_core.signals.queue_index_book"):
            book = self.create_book(
                isbn="9780000000012",
                is_published=True,
                file_url="https://cdn.example/book-v1.pdf",
            )

        BookDocumentIndex.objects.create(
            book=book,
            status=BookDocumentIndex.Status.SUCCESS,
            vector_ids=["vec-old"],
        )

        with patch("ai_core.signals.queue_delete_book_vectors") as mock_delete, patch(
            "ai_core.signals.queue_index_book"
        ) as mock_index:
            with self.captureOnCommitCallbacks(execute=True):
                book.file_url = "https://cdn.example/book-v2.pdf"
                book.save()

        mock_delete.assert_called_once_with(book.id, vector_ids=["vec-old"])
        mock_index.assert_called_once_with(book.id)

    def test_delete_queues_cleanup(self):
        with patch("ai_core.signals.queue_index_book"):
            book = self.create_book(
                isbn="9780000000013",
                is_published=True,
                file_url="https://cdn.example/book.pdf",
            )

        BookDocumentIndex.objects.create(
            book=book,
            status=BookDocumentIndex.Status.SUCCESS,
            vector_ids=["vec-z"],
        )

        with patch("ai_core.signals.queue_delete_book_vectors") as mock_delete:
            book_id = book.id
            book.delete()

        mock_delete.assert_called_once_with(book_id, vector_ids=["vec-z"])


class ScopedRetrievalTests(SimpleTestCase):
    def test_build_filter_single_book_scope(self):
        metadata_filter = ai_service._build_filter(book_id=7, allowed_book_ids=[7, 9])
        self.assertEqual(metadata_filter, {"book_id": "7"})

    def test_build_filter_allowed_books_scope(self):
        metadata_filter = ai_service._build_filter(allowed_book_ids=[1, 2, 3])
        self.assertEqual(metadata_filter, {"book_id": {"$in": ["1", "2", "3"]}})

    def test_build_filter_rejects_out_of_scope_book(self):
        with self.assertRaises(PermissionError):
            ai_service._build_filter(book_id=4, allowed_book_ids=[1, 2, 3])


class LibrisChatEndpointTests(BaseBookTestCase, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("libris-chat")
        self.user = User.objects.create_user(
            email="reader@example.com",
            password="password123",
            name="Reader User",
        )
        self.indexed_book = self.create_book(
            isbn="9780000000021",
            is_published=True,
            file_url=None,
        )
        self.unindexed_book = self.create_book(
            isbn="9780000000022",
            is_published=True,
            file_url=None,
        )
        self.unpublished_book = self.create_book(
            isbn="9780000000023",
            is_published=False,
            file_url=None,
        )
        BookDocumentIndex.objects.create(
            book=self.indexed_book,
            status=BookDocumentIndex.Status.SUCCESS,
            vector_ids=["vec-1"],
            chunk_count=1,
            page_count=1,
        )

    def test_auth_required(self):
        response = self.client.post(self.url, {"message": "Hello"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_missing_message_validation(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "No message provided")

    def test_invalid_book_id_validation(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {"message": "Hello", "book_id": "abc"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_unpublished_book_rejected(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {"message": "Hello", "book_id": self.unpublished_book.id},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_unindexed_book_rejected(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {"message": "Hello", "book_id": self.unindexed_book.id},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_allowed_scope_validation(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {
                "message": "Hello",
                "book_id": self.indexed_book.id,
                "allowed_book_ids": [self.unindexed_book.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    @patch("ai_core.views.ask_libris", return_value="Scoped answer")
    def test_scoped_query_success(self, mock_ask_libris):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {"message": "What is this about?", "book_id": self.indexed_book.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["answer"], "Scoped answer")
        mock_ask_libris.assert_called_once_with(
            "What is this about?",
            book_id=self.indexed_book.id,
            allowed_book_ids=[self.indexed_book.id],
        )
