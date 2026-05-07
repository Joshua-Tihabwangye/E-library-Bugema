import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor

from django.db import close_old_connections

from .document_ingestion import delete_book_vectors, index_book_by_id


logger = logging.getLogger(__name__)

_MAX_WORKERS = max(1, int(os.getenv("AI_INDEXING_WORKERS", "2")))
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="ai-index")


def _run_with_db_cleanup(func, *args, **kwargs):
    close_old_connections()
    try:
        return func(*args, **kwargs)
    finally:
        close_old_connections()


def _submit(func, *args, **kwargs) -> Future:
    try:
        return _EXECUTOR.submit(_run_with_db_cleanup, func, *args, **kwargs)
    except RuntimeError:
        # Fallback during interpreter shutdown or executor teardown.
        logger.warning("AI task executor unavailable; running task inline.")
        future: Future = Future()
        try:
            result = _run_with_db_cleanup(func, *args, **kwargs)
            future.set_result(result)
        except Exception as exc:
            future.set_exception(exc)
        return future


def queue_index_book(book_id: int) -> Future:
    """
    Async entrypoint: queue indexing for a single book.
    """
    logger.info("Queueing Q&A indexing for book_id=%s", book_id)
    return _submit(index_book_by_id, book_id)


def queue_delete_book_vectors(book_id: int, vector_ids=None) -> Future:
    """
    Async entrypoint: queue vector deletion for a single book.
    """
    logger.info("Queueing Q&A vector cleanup for book_id=%s", book_id)
    return _submit(delete_book_vectors, book_id, vector_ids=vector_ids)
