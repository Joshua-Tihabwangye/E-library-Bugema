from django.shortcuts import render
# ai_core/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .ai_service import ask_libris

class LibrisChatView(APIView):
    """
    API endpoint that allows users to chat with Libris.
    """
    def post(self, request):
        user_query = request.data.get("message")
        
        if not user_query:
            return Response(
                {"error": "No message provided"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Call our RAG logic
            answer = ask_libris(user_query)
            return Response({"answer": answer}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {"error": str(e)}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# Create your views here.
