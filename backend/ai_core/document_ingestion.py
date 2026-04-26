import os
import tempfile
from contextlib import suppress

import requests
from django.utils import timezone
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from catalog.models import Book

from .models import BookDocumentIndex
from .vector_store import get_vector_store


DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200
DOWNLOAD_TIMEOUT_SECONDS = 60


class DocumentIndexingError(Exception):
    """Raised when a book cannot be indexed for Q&A."""


def _resolve_source_url(book: Book) -> str:
    if book.file and getattr(book.file, "url", None):
        return book.file.url
    if book.file_url:
        return book.file_url
    raise DocumentIndexingError(
        f"Book {book.id} has no accessible file URL for document indexing."
    )


def _download_pdf(book: Book) -> tuple[str, str]:
    source_url = _resolve_source_url(book)
    suffix = os.path.splitext(source_url.split("?")[0])[1] or ".pdf"

    response = requests.get(source_url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        temp_file.write(response.content)
        temp_file.flush()
    finally:
        temp_file.close()

    return temp_file.name, source_url


def _load_pdf_pages(temp_path: str, book: Book, source_url: str) -> list[Document]:
    try:
        reader = PdfReader(temp_path)
    except Exception as exc:
        raise DocumentIndexingError(
            f"Unable to open PDF for book {book.id}: {exc}"
        ) from exc

    documents = []
    for page_index, page in enumerate(reader.pages, start=1):
        with suppress(Exception):
            page_text = page.extract_text()
            if page_text and page_text.strip():
                documents.append(
                    Document(
                        page_content=page_text.strip(),
                        metadata={
                            "book_id": str(book.id),
                            "title": book.title,
                            "author": book.author.name if book.author else "",
                            "isbn": book.isbn,
                            "page": page_index,
                            "chapter": None,
                            "source_url": source_url,
                            "file_type": book.file_type,
                        },
                    )
                )

    if not documents:
        raise DocumentIndexingError(
            f"Book {book.id} produced no extractable PDF text."
        )

    return documents


def _split_documents(documents: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(documents)


def _build_vector_ids(book: Book, chunks: list[Document]) -> list[str]:
    vector_ids = []
    page_chunk_counters: dict[int, int] = {}

    for chunk in chunks:
        page_number = int(chunk.metadata.get("page") or 0)
        page_chunk_counters[page_number] = page_chunk_counters.get(page_number, 0) + 1
        chunk_index = page_chunk_counters[page_number]
        chunk.metadata["chunk_index"] = chunk_index
        chunk.metadata["book_id"] = str(book.id)
        vector_ids.append(f"book-{book.id}-page-{page_number}-chunk-{chunk_index}")

    return vector_ids


def index_book_document(book: Book) -> BookDocumentIndex:
    if book.file_type != "PDF":
        raise DocumentIndexingError(
            f"Book {book.id} is {book.file_type}; only PDF ingestion is implemented."
        )

    vector_store = get_vector_store()
    index_record, _ = BookDocumentIndex.objects.get_or_create(book=book)
    temp_path = None

    try:
        temp_path, source_url = _download_pdf(book)
        page_documents = _load_pdf_pages(temp_path, book, source_url)
        chunk_documents = _split_documents(page_documents)
        vector_ids = _build_vector_ids(book, chunk_documents)

        if index_record.vector_ids:
            vector_store.delete(ids=index_record.vector_ids)

        vector_store.add_documents(documents=chunk_documents, ids=vector_ids)

        index_record.status = BookDocumentIndex.Status.SUCCESS
        index_record.source_url = source_url
        index_record.vector_ids = vector_ids
        index_record.chunk_count = len(chunk_documents)
        index_record.page_count = len(page_documents)
        index_record.last_error = None
        index_record.indexed_at = timezone.now()
        index_record.save(
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
        return index_record
    except Exception as exc:
        error_message = str(exc)
        index_record.status = BookDocumentIndex.Status.FAILED
        index_record.last_error = error_message
        index_record.save(update_fields=["status", "last_error", "updated_at"])
        raise
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def index_books_for_qa(books) -> dict[str, int]:
    indexed = 0
    failed = 0

    for book in books:
        try:
            index_book_document(book)
            indexed += 1
        except Exception:
            failed += 1

    return {"indexed": indexed, "failed": failed}
