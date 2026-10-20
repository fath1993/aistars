"""
Activity recording.

One helper, called at points that mean something to an operator. Recording
never raises: an observability system that can take down the thing it
observes is worse than none.
"""

import logging

logger = logging.getLogger(__name__)


def record(category, event, message="", *, level=None, account=None,
           detail=None, duration_ms=None):
    from .models import ActivityLog, LogLevel

    try:
        return ActivityLog.objects.create(
            level=level or LogLevel.INFO,
            category=category,
            event=event,
            message=str(message)[:4000],
            account=account,
            detail=detail or {},
            duration_ms=duration_ms,
        )
    except Exception:
        # Includes the case where the table does not exist yet.
        logger.exception("could not write activity log %s/%s", category, event)
        return None


def info(category, event, message="", **kwargs):
    return record(category, event, message, **kwargs)


def warn(category, event, message="", **kwargs):
    from .models import LogLevel

    logger.warning("%s/%s %s", category, event, message)
    return record(category, event, message, level=LogLevel.WARNING, **kwargs)


def error(category, event, message="", **kwargs):
    from .models import LogLevel

    logger.error("%s/%s %s", category, event, message)
    return record(category, event, message, level=LogLevel.ERROR, **kwargs)


def purge(days: int) -> int:
    from datetime import timedelta

    from django.utils import timezone

    from .models import ActivityLog

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = ActivityLog.objects.filter(created_at__lt=cutoff).delete()
    return deleted
