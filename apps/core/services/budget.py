"""
Spend guard for the paid AI APIs. Entirely deterministic.

Ported from AVideo through MyShortClip. Every call is checked before it is
sent against the most it can cost (a chat answer's longest length, an
image's size and quality), and recorded afterwards at what it really cost.

Design rules kept from AVideo:

  Fail closed.        Zero means stop, not unlimited.
  Persist the ledger. In the database, not only the cache -- a cache flush
                      must not re-arm the day's budget.
  Two windows.        A daily cap, plus an hourly cap that catches a runaway
                      inside the hour it starts.
"""

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


class BudgetError(Exception):
    """
    Refused on budget grounds. `kind` and `retry_at` exist so the user can be
    told something useful rather than a log line.
    """

    kind = "budget"

    def __init__(self, message, retry_at=None):
        super().__init__(message)
        self.retry_at = retry_at


class PlatformPaused(BudgetError):
    kind = "paused"


class CapExceeded(BudgetError):
    kind = "cap"


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------

def record_spend(cost_micros: int) -> None:
    """Record one billed request in the current hour's bucket."""
    from ..models import SpendLedger

    now = timezone.now()
    amount = max(int(cost_micros), 0)
    fields = {"calls": F("calls") + 1, "cost_micros": F("cost_micros") + amount}

    updated = SpendLedger.objects.filter(date=now.date(), hour=now.hour).update(**fields)
    if updated:
        return
    try:
        with transaction.atomic():
            SpendLedger.objects.create(date=now.date(), hour=now.hour, calls=1,
                                       cost_micros=amount)
    except IntegrityError:
        # Another worker created the bucket between the update and the insert.
        SpendLedger.objects.filter(date=now.date(), hour=now.hour).update(**fields)


def _sum(**filters) -> dict:
    from ..models import SpendLedger

    row = SpendLedger.objects.filter(**filters).aggregate(
        cost=Sum("cost_micros"), calls=Sum("calls"))
    return {k: v or 0 for k, v in row.items()}


def spent_today() -> dict:
    return _sum(date=timezone.now().date())


def spent_this_hour() -> dict:
    now = timezone.now()
    return _sum(date=now.date(), hour=now.hour)


def purge_old_ledger(days: int = 90) -> int:
    from ..models import SpendLedger

    cutoff = timezone.now().date() - timedelta(days=days)
    deleted, _ = SpendLedger.objects.filter(date__lt=cutoff).delete()
    return deleted


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def _next_hour():
    now = timezone.now()
    return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def _next_midnight():
    now = timezone.now()
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                             microsecond=0)


def capacity() -> dict:
    """What is left right now, for the admin and status screens."""
    from ..models import ControlPanel

    panel = ControlPanel.get()
    hour, day = spent_this_hour(), spent_today()
    return {
        "hour_spent_micros": hour["cost"],
        "hour_cap_micros": panel.hourly_cap_micros(),
        "hour_calls": hour["calls"],
        "day_spent_micros": day["cost"],
        "day_cap_micros": panel.daily_cap_micros(),
        "day_calls": day["calls"],
        "paused": panel.paused,
        "hour_resets_at": _next_hour().isoformat(),
        "day_resets_at": _next_midnight().isoformat(),
    }


def assert_can_spend(cost_micros: int = 0) -> None:
    """
    Raise unless `cost_micros` more may be spent now. Checked before every
    paid request.

    Negative caps disable. Zero is a HARD STOP, not "unlimited": an owner who
    types 0 into a budget field means "spend nothing".
    """
    from ..models import ControlPanel

    panel = ControlPanel.get()
    if panel.paused:
        raise PlatformPaused(panel.pause_reason or "paused in the admin")

    cost = max(int(cost_micros), 0)

    daily = panel.daily_cap_micros()
    if daily >= 0:
        spent = spent_today()["cost"]
        if daily == 0 or spent + cost > daily:
            raise CapExceeded(
                f"daily cap: {spent / 1e6:.2f} + {cost / 1e6:.2f} of "
                f"{daily / 1e6:.2f} USD", retry_at=_next_midnight())

    hourly = panel.hourly_cap_micros()
    if hourly >= 0:
        this_hour = spent_this_hour()["cost"]
        if hourly == 0 or this_hour + cost > hourly:
            raise CapExceeded(
                f"hourly cap: {this_hour / 1e6:.2f} + {cost / 1e6:.2f} of "
                f"{hourly / 1e6:.2f} USD", retry_at=_next_hour())


def pause(reason: str) -> None:
    from ..models import ControlPanel

    panel = ControlPanel.get()
    panel.paused = True
    panel.pause_reason = reason[:255]
    panel.paused_at = timezone.now()
    panel.save()


def resume() -> None:
    from ..models import ControlPanel

    panel = ControlPanel.get()
    panel.paused = False
    panel.pause_reason = ""
    panel.paused_at = None
    panel.save()
