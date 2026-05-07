import os
import tempfile
from contextlib import suppress
import logging
import random
import re
import time

import requests
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from catalog.models import Book

from .models import BookDocumentIndex
from .vector_store import get_vector_store


DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200
DOWNLOAD_TIMEOUT_SECONDS = 60
EMBEDDING_MAX_RETRIES = 5
EMBEDDING_BASE_BACKOFF_SECONDS = 2.0

logger = logging.getLogger(__name__)


class DocumentIndexingError(Exception):
    """Raised when a book cannot be indexed for Q&A."""


def _parse_retry_delay_seconds(error_message: str):
    """
    Parse provider retry hints like:
    - "Please retry in 52.68852778s."
    - "retryDelay': '36s'"
    """
    patterns = [
        r"Please retry in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retryDelay['\"]?\s*:\s*['\"]([0-9]+(?:\.[0-9]+)?)s['\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, error_message)
        if match:
            with suppress(Exception):
                return float(match.group(1))
    return None


def _is_rate_limit_error(error_message: str) -> bool:
    lower = error_message.lower()
    return (
        "resource_exhausted" in lower
        or "quota exceeded" in lower
        or "429" in lower
        or "rate limit" in lower
    )


def _add_documents_with_retry(vector_store, chunk_documents, vector_ids):
    """
    Retry vector insertion when embedding provider returns transient quota/rate-limit errors.
    """
    last_exc = None

    for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
        try:
            vector_store.add_documents(documents=chunk_documents, ids=vector_ids)
            return
        except Exception as exc:
            last_exc = exc
            error_message = str(exc)
            if not _is_rate_limit_error(error_message) or attempt == EMBEDDING_MAX_RETRIES:
                raise

            retry_hint = _parse_retry_delay_seconds(error_message)
            if retry_hint is None:
                retry_hint = EMBEDDING_BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            jitter = random.uniform(0.1, 0.7)
            sleep_seconds = min(retry_hint + jitter, 90.0)

            logger.warning(
                "Rate limited while embedding chunks (attempt %s/%s). Retrying in %.2fs.",
                attempt,
                EMBEDDING_MAX_RETRIES,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)

    if last_exc:
        raise last_exc


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

        _add_documents_with_retry(vector_store, chunk_documents, vector_ids)
        index_record.mark_success(
            source_url=source_url,
            vector_ids=vector_ids,
            chunk_count=len(chunk_documents),
            page_count=len(page_documents),
        )
        return index_record
    except Exception as exc:
        error_message = str(exc)
        index_record.mark_failed(error_message)
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


def index_book_by_id(book_id: int) -> BookDocumentIndex:
    """
    Load a book and index it into the Q&A vector store.
    """
    book = Book.objects.select_related("author").get(pk=book_id)
    return index_book_document(book)


def delete_book_vectors(book_id: int, vector_ids=None) -> bool:
    """
    Remove a book's vectors from the Q&A collection and reset index state.
    Returns True if deletion was attempted against existing vectors.
    """
    index_record = BookDocumentIndex.objects.filter(book_id=book_id).first()
    normalized_vector_ids = list(vector_ids or [])
    if not normalized_vector_ids and index_record:
        normalized_vector_ids = list(index_record.vector_ids or [])

    vector_store = get_vector_store()
    deleted = False
    try:
        if normalized_vector_ids:
            vector_store.delete(ids=normalized_vector_ids)
            deleted = True
        elif not index_record:
            # Best-effort fallback for records that no longer have stored vector IDs.
            with suppress(Exception):
                vector_store.delete(filter={"book_id": str(book_id)})
                deleted = True
    except Exception:
        logger.exception("Failed deleting Q&A vectors for book_id=%s", book_id)
        raise

    if index_record:
        index_record.reset_to_pending()

    return deleted
