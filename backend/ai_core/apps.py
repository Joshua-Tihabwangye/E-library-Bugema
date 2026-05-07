from django.apps import AppConfig


class AiCoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ai_core'

    def ready(self):
        # Register signal handlers for automatic Q&A indexing lifecycle.
        from . import signals  # noqa: F401
