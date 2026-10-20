from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_runtime_settings(sender, **kwargs):
    from . import runtime

    runtime.sync_registry()


def _sync_texts(sender, using="default", **kwargs):
    """Record the texts now in the code, and start translating new ones."""
    import logging

    from django.db import connections

    from . import i18n, tasks
    from .models import SourceText

    # A partial migrate (to an older migration, e.g. a rollback) leaves the
    # table missing; there is nothing to record then.
    if SourceText._meta.db_table not in connections[using].introspection.table_names():
        return
    if i18n.sync_sources():
        try:
            tasks.translate_missing.delay()
        except Exception:  # broker down during a deploy: the beat retries
            logging.getLogger(__name__).warning("could not queue translations")


def _translations_changed(sender, **kwargs):
    from . import i18n

    i18n.changed()


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "AIstars"

    def ready(self):
        # One row per registry entry after every migrate, so adding a setting
        # is a registry line rather than a data migration.
        post_migrate.connect(_sync_runtime_settings, sender=self)
        # After every deploy (the entrypoint migrates), the texts in the code
        # are recorded and new ones are translated in the background.
        post_migrate.connect(_sync_texts, sender=self)

        from django.db.models.signals import post_delete, post_save

        from .models import Translation

        # Any edit, by AI or by hand, reaches every process's catalog.
        post_save.connect(_translations_changed, sender=Translation)
        post_delete.connect(_translations_changed, sender=Translation)
