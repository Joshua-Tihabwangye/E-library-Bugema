# ai_core/urls.py
from django.urls import path
from .views import LibrisChatView

urlpatterns = [
    path('chat/', LibrisChatView.as_view(), name='libris-chat'),
]