from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from catalog.models import Book

from .ai_service import ask_libris
from .models import BookDocumentIndex


class LibrisChatView(APIView):
    """
    API endpoint that allows users to chat with Libris.
    """

    @staticmethod
    def _parse_optional_int(value, field_name: str):
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer.") from exc

    @staticmethod
    def _parse_allowed_book_ids(value):
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("allowed_book_ids must be a list of integers.")

        normalized_ids = []
        for raw_id in value:
            try:
                normalized_ids.append(int(raw_id))
            except (TypeError, ValueError) as exc:
                raise ValueError("allowed_book_ids must contain only integers.") from exc
        return list(dict.fromkeys(normalized_ids))

    @staticmethod
    def _indexed_published_ids(book_ids=None):
        qs = BookDocumentIndex.objects.filter(
            status=BookDocumentIndex.Status.SUCCESS,
            book__is_published=True,
        )
        if book_ids is not None:
            qs = qs.filter(book_id__in=book_ids)
        return list(qs.values_list("book_id", flat=True))

    def post(self, request):
        user_query = request.data.get("message")

        if not user_query:
            return Response(
                {"error": "No message provided"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            requested_book_id = self._parse_optional_int(
                request.data.get("book_id"),
                "book_id",
            )
            requested_allowed_ids = self._parse_allowed_book_ids(
                request.data.get("allowed_book_ids")
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if requested_book_id is not None:
            is_published = Book.objects.filter(pk=requested_book_id, is_published=True).exists()
            if not is_published:
                return Response(
                    {"error": "Book not found or not published."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if requested_allowed_ids is not None and requested_book_id not in requested_allowed_ids:
                return Response(
                    {"error": "Requested book is outside the allowed scope."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            indexed_ids = set(self._indexed_published_ids([requested_book_id]))
            if requested_book_id not in indexed_ids:
                return Response(
                    {"error": "Book is not indexed for Q&A yet."},
                    status=status.HTTP_409_CONFLICT,
                )

            scope_book_id = requested_book_id
            scope_allowed_ids = [requested_book_id]
        else:
            if requested_allowed_ids is None:
                scope_allowed_ids = self._indexed_published_ids()
            else:
                allowed_indexed_ids = set(self._indexed_published_ids(requested_allowed_ids))
                disallowed_ids = sorted(set(requested_allowed_ids) - allowed_indexed_ids)
                if disallowed_ids:
                    return Response(
                        {
                            "error": "Some requested books are not published, indexed, or allowed.",
                            "book_ids": disallowed_ids,
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )
                scope_allowed_ids = sorted(allowed_indexed_ids)

            if not scope_allowed_ids:
                return Response(
                    {"error": "No indexed published books available for Q&A scope."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scope_book_id = None

        try:
            answer = ask_libris(
                user_query,
                book_id=scope_book_id,
                allowed_book_ids=scope_allowed_ids,
            )
            return Response({"answer": answer}, status=status.HTTP_200_OK)
        except PermissionError:
            return Response(
                {"error": "Requested scope is not allowed."},
                status=status.HTTP_403_FORBIDDEN,
            )
        except Exception:
            return Response(
                {"error": "Failed to process Q&A request."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
