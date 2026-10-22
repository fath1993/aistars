import calendar

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.core import runtime
from apps.core.services import telegram

from . import money
from .models import Bill


@shared_task
def warn_low_balances():
    """Hourly: tell the owner when little is left at an AI provider (once a day each)."""
    owner = runtime.get("ADMIN_CHAT_ID")
    if not owner or not float(runtime.get("PROVIDER_LOW_BALANCE_USD")):
        return 0
    told = 0
    for row in money.provider_balances():
        if not row["low"] or not cache.add(f"finance:low:{row['payee']}", 1, 60 * 60 * 24):
            continue
        telegram.send_message(owner, (
            f"⚠️ <b>{row['name']}</b>: about ${row['left']:.2f} left of what you paid "
            f"(${row['paid']:.2f} since {row['since']:%d %b}, ${row['used']:.2f} used by the "
            f"bot). Top it up, then add the payment in the admin: My finances → Bills."))
        told += 1
    return told


@shared_task
def repeat_monthly_bills():
    """Daily: each bill marked Repeats monthly gets this month's copy, unpaid, for its invoice."""
    today = timezone.localdate()
    made = 0
    for bill in Bill.objects.filter(repeats_monthly=True, repeat_of=None):
        if (bill.date.year, bill.date.month) >= (today.year, today.month):
            continue
        if bill.repeats.filter(date__year=today.year, date__month=today.month).exists():
            continue
        day = min(bill.date.day, calendar.monthrange(today.year, today.month)[1])
        if today.day < day:
            continue
        Bill.objects.create(date=today.replace(day=day), payee=bill.payee,
                            description=bill.description, amount=bill.amount,
                            currency=bill.currency, paid=False, repeat_of=bill)
        made += 1
    return made
