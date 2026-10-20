from django.apps import AppConfig


class SafetyConfig(AppConfig):
    """The checks every request passes before anything is made."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.safety"
    verbose_name = "Safety"
