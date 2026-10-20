from django.apps import AppConfig
from django.db.models.signals import post_migrate, post_save


def _record_texts(sender, using="default", **kwargs):
    """The database texts users see, recorded for translation."""
    from django.db import connections

    from apps.core.models import SourceText

    from .models import Assistant

    tables = connections[using].introspection.table_names()
    if SourceText._meta.db_table in tables and Assistant._meta.db_table in tables:
        from . import catalog

        catalog.record()


def _text_changed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from . import catalog

    catalog.record()


class StudioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.studio"
    verbose_name = "AI tools"

    def ready(self):
        from apps.ai.models import AIModel

        from .models import Assistant

        post_migrate.connect(_record_texts, sender=self)
        # A tagline or an assistant edited in the admin reaches the translator.
        post_save.connect(_text_changed, sender=Assistant)
        post_save.connect(_text_changed, sender=AIModel)
