from celery import shared_task
from django.conf import settings

from . import activity, runtime
from .services import budget


@shared_task
def purge_activity_log():
    return activity.purge(runtime.get("ACTIVITY_LOG_RETENTION_DAYS"))


@shared_task
def purge_spend_ledger():
    return budget.purge_old_ledger(settings.SPEND_LEDGER_RETENTION_DAYS)


@shared_task
def translate_language(code, model_id=None, overwrite=False, overwrite_manual=False):
    """One language at a time: a second request while one runs is dropped."""
    from django.core.cache import cache

    from . import translation

    lock = f"i18n:translating:{code}"
    if not cache.add(lock, 1, 60 * 60):
        return "already running"
    try:
        return translation.run(code, model_id=model_id, overwrite=overwrite,
                               overwrite_manual=overwrite_manual)
    finally:
        cache.delete(lock)


@shared_task
def translate_missing():
    """
    Every language with Translate on that still has untranslated texts gets a
    run. After a deploy adds texts, or after an API key arrives, nothing has
    to be pressed. Languages that failed wait for the next half hour.
    """
    from . import translation
    from .models import Language, TranslationStatus

    started = []
    for language in Language.objects.filter(translate=True).exclude(code="en").exclude(
            status__in=[TranslationStatus.QUEUED, TranslationStatus.RUNNING]):
        if not translation.missing_count(language):
            continue
        try:
            translation.model_for(language)
        except translation.TranslationError as exc:
            # No usable model yet (usually no API key): say so on the language,
            # quietly, instead of a failed run and a log line every 30 minutes.
            Language.objects.filter(pk=language.pk).update(
                status=TranslationStatus.FAILED, status_detail=str(exc)[:255])
            continue
        translation.queue(language.code)
        started.append(language.code)
    return started
