from django.apps import AppConfig


class AIConfig(AppConfig):
    """The AI providers, their API keys and models, used by every AI feature."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai"
    verbose_name = "AI"
