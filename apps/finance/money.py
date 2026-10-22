"""
The owner's money, in USD: what came in (Stars users paid, less refunds),
what went out (the AI providers, call by call, and the bills), what is left
at each provider, and what users still hold in credits. EUR bills are
converted at FINANCE_EUR_RATE.
"""

import calendar
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Min, OuterRef, Subquery, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.ai.models import USER_PURPOSES, AICall, Purpose
from apps.billing.models import LedgerEntry, Payment, PaymentStatus
from apps.core import runtime
from apps.core.models import Account, SpendLedger

from .models import PROVIDERS, Bill, Currency, Payee

CENT = Decimal("0.01")


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

def star_value() -> Decimal:
    return Decimal(str(runtime.get("STAR_VALUE_USD")))


def eur_rate() -> Decimal:
    """USD for one EUR."""
    return Decimal(str(runtime.get("FINANCE_EUR_RATE")))


def stars_usd(stars) -> Decimal:
    return Decimal(stars or 0) * star_value()


def micros_usd(micros) -> Decimal:
    return Decimal(int(micros or 0)) / 1_000_000


def bill_usd(bill) -> Decimal:
    return bill.amount * eur_rate() if bill.currency == Currency.EUR else bill.amount


def in_eur(usd) -> Decimal:
    return usd / eur_rate()


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------

def month_range(year, month) -> tuple:
    """(start, end) of a month, local time."""
    zone = timezone.get_current_timezone()
    start = timezone.make_aware(datetime(year, month, 1), zone)
    days = calendar.monthrange(year, month)[1]
    return start, start + timedelta(days=days)


def all_time() -> tuple:
    zone = timezone.get_current_timezone()
    return timezone.make_aware(datetime(2000, 1, 1), zone), timezone.now() + timedelta(days=1)


def months(limit=24) -> list:
    """(year, month), newest first: from the first payment, AI call or bill to now."""
    firsts = [Payment.objects.aggregate(first=Min("created_at"))["first"],
              AICall.objects.aggregate(first=Min("at"))["first"]]
    first_bill = Bill.objects.aggregate(first=Min("date"))["first"]
    if first_bill:
        firsts.append(timezone.make_aware(datetime.combine(first_bill, time())))
    now = timezone.localtime()
    first = min([timezone.localtime(moment) for moment in firsts if moment] or [now])
    found, year, month = [], now.year, now.month
    while (year, month) >= (first.year, first.month) and len(found) < limit:
        found.append((year, month))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return found


# --------------------------------------------------------------------------
# The overview
# --------------------------------------------------------------------------

PURPOSE_NAMES = dict(Purpose.choices)
PROVIDER_NAMES = dict(Payee.choices)


def overview(start, end) -> dict:
    """Everything the overview page shows for one period."""
    money_in = income(start, end)
    ai = ai_costs(start, end)
    bills = list(Bill.objects.filter(date__gte=timezone.localtime(start).date(),
                                     date__lt=timezone.localtime(end).date())
                 .exclude(payee__in=PROVIDERS))
    for bill in bills:
        bill.usd = bill_usd(bill).quantize(CENT)
    other = sum((bill.usd for bill in bills), Decimal(0))
    profit = money_in["net"] - ai["total"] - other
    return {
        "income": money_in, "ai": ai, "bills": bills, "other": other,
        "costs": ai["total"] + other, "profit": profit,
        "margin": (profit / money_in["net"] * 100) if money_in["net"] > 0 else None,
    }


def income(start, end) -> dict:
    paid = Payment.objects.filter(created_at__gte=start, created_at__lt=end)
    totals = paid.aggregate(stars=Sum("stars"), payments=Count("id"))
    refunded = Payment.objects.filter(status=PaymentStatus.REFUNDED, refunded_at__gte=start,
                                      refunded_at__lt=end).aggregate(stars=Sum("stars"),
                                                                     payments=Count("id"))
    stars, back = totals["stars"] or 0, refunded["stars"] or 0
    return {"stars": stars, "payments": totals["payments"],
            "buyers": paid.values("account").distinct().count(),
            "refunded_stars": back, "refunds": refunded["payments"],
            "gross": stars_usd(stars), "refunded": stars_usd(back),
            "net": stars_usd(stars - back)}


def ai_costs(start, end) -> dict:
    """By provider and by purpose; before calls were recorded one by one, the hourly totals."""
    calls = AICall.objects.filter(at__gte=start, at__lt=end)
    by_provider = [{"name": PROVIDER_NAMES.get(row["provider"], row["provider"]),
                    "calls": row["calls"], "usd": micros_usd(row["cost"])}
                   for row in calls.values("provider").annotate(
                       cost=Sum("cost_micros"), calls=Count("id")).order_by("-cost")]
    users, own = [], []
    for row in calls.values("purpose").annotate(cost=Sum("cost_micros"), calls=Count("id")) \
            .order_by("-cost"):
        line = {"name": PURPOSE_NAMES.get(row["purpose"], row["purpose"]),
                "calls": row["calls"], "usd": micros_usd(row["cost"])}
        (users if row["purpose"] in USER_PURPOSES else own).append(line)
    earlier = _not_itemised(start, end)
    total = sum((line["usd"] for line in by_provider), Decimal(0)) + earlier
    return {"by_provider": by_provider, "users": users, "own": own, "earlier": earlier,
            "users_total": sum((line["usd"] for line in users), Decimal(0)),
            "own_total": sum((line["usd"] for line in own), Decimal(0)), "total": total}


def _not_itemised(start, end) -> Decimal:
    """AI spend from before the calls were recorded one by one (hourly totals only)."""
    first = AICall.objects.aggregate(first=Min("at"))["first"]
    cutoff = min(end, first) if first else end
    if cutoff <= start:
        return Decimal(0)
    rows = SpendLedger.objects.filter(date__gte=start.date(), date__lt=cutoff.date())
    return micros_usd(rows.aggregate(cost=Sum("cost_micros"))["cost"])


def daily(start, end) -> list:
    """Day by day, newest first: income, refunds and AI costs."""
    days = {}

    def day(moment):
        return days.setdefault(moment, {"day": moment, "income": Decimal(0),
                                        "refunded": Decimal(0), "ai": Decimal(0), "calls": 0})

    for row in Payment.objects.filter(created_at__gte=start, created_at__lt=end).annotate(
            day=TruncDate("created_at")).values("day").annotate(stars=Sum("stars")):
        day(row["day"])["income"] = stars_usd(row["stars"])
    for row in Payment.objects.filter(status=PaymentStatus.REFUNDED, refunded_at__gte=start,
                                      refunded_at__lt=end).annotate(
            day=TruncDate("refunded_at")).values("day").annotate(stars=Sum("stars")):
        day(row["day"])["refunded"] = stars_usd(row["stars"])
    for row in AICall.objects.filter(at__gte=start, at__lt=end).annotate(
            day=TruncDate("at")).values("day").annotate(cost=Sum("cost_micros"),
                                                        calls=Count("id")):
        line = day(row["day"])
        line["ai"], line["calls"] = micros_usd(row["cost"]), row["calls"]
    for line in days.values():
        line["result"] = line["income"] - line["refunded"] - line["ai"]
    return sorted(days.values(), key=lambda line: line["day"], reverse=True)


# --------------------------------------------------------------------------
# What is left at the providers, and what users hold
# --------------------------------------------------------------------------

def provider_balances() -> list:
    """
    For each provider paid at least once: paid (bills) since the first
    payment, used by the bot since then (AI calls), and the difference.
    """
    low = Decimal(str(runtime.get("PROVIDER_LOW_BALANCE_USD")))
    found = []
    for payee in PROVIDERS:
        bills = list(Bill.objects.filter(payee=payee))
        if not bills:
            continue
        since = min(bill.date for bill in bills)
        zone = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(since, time()), zone)
        paid = sum((bill_usd(bill) for bill in bills), Decimal(0))
        used = micros_usd(AICall.objects.filter(provider=payee, at__gte=start)
                          .aggregate(cost=Sum("cost_micros"))["cost"])
        left = paid - used
        found.append({"payee": payee, "name": PROVIDER_NAMES[payee], "since": since,
                      "paid": paid, "used": used, "left": left, "low": left < low})
    return found


def held_credits() -> dict:
    """Credits users bought or were given and have not used, and what they can still cost."""
    newest = LedgerEntry.objects.filter(account=OuterRef("pk")).order_by("-id")
    credits = Account.objects.annotate(balance=Subquery(newest.values("balance_after")[:1])) \
        .filter(balance__gt=0).aggregate(total=Sum("balance"))["total"] or 0
    margin = Decimal(str(runtime.get("PROFIT_MARGIN_PERCENT")))
    return {"credits": credits, "paid": stars_usd(credits),
            "cost_at_most": stars_usd(credits) / (1 + margin / 100)}


def openai_billed(start, end):
    """(micro-USD OpenAI itself reports, or None; the reason when it could not be read)."""
    from apps.ai import clients
    from apps.ai.models import Provider

    provider = Provider.objects.filter(kind="openai").first()
    if provider is None or not provider.admin_key.strip():
        return None, ""
    try:
        return clients.billed_costs(provider, start, min(end, timezone.now())), ""
    except clients.AIError as exc:
        return None, str(exc)


# --------------------------------------------------------------------------
# For the accountant
# --------------------------------------------------------------------------

def csv_rows(start, end) -> list:
    """One row per payment, refund, day of AI costs and bill: date, kind, what, USD."""
    rows = [("date", "kind", "what", "amount (USD)", "original amount")]
    for payment in Payment.objects.filter(created_at__gte=start, created_at__lt=end) \
            .select_related("account").order_by("created_at"):
        rows.append((timezone.localtime(payment.created_at).date(), "income",
                     f"Stars payment from {payment.account} ({payment.telegram_charge_id})",
                     _money(stars_usd(payment.stars)), f"{payment.stars} Stars"))
    for payment in Payment.objects.filter(status=PaymentStatus.REFUNDED, refunded_at__gte=start,
                                          refunded_at__lt=end).select_related("account"):
        rows.append((timezone.localtime(payment.refunded_at).date(), "refund",
                     f"Stars refunded to {payment.account} ({payment.telegram_charge_id})",
                     _money(-stars_usd(payment.stars)), f"-{payment.stars} Stars"))
    for row in AICall.objects.filter(at__gte=start, at__lt=end).annotate(
            day=TruncDate("at")).values("day", "provider").annotate(
            cost=Sum("cost_micros"), calls=Count("id")).order_by("day", "provider"):
        rows.append((row["day"], "AI cost",
                     f"{PROVIDER_NAMES.get(row['provider'], row['provider'])}: "
                     f"{row['calls']} calls", _money(-micros_usd(row["cost"])), ""))
    for bill in Bill.objects.filter(date__gte=timezone.localtime(start).date(),
                                    date__lt=timezone.localtime(end).date()).order_by("date"):
        kind = "paid to AI provider" if bill.payee in PROVIDERS else "cost"
        rows.append((bill.date, kind, " ".join(part for part in (
            bill.get_payee_display(), bill.description, bill.invoice_number) if part),
            _money(-bill_usd(bill)), f"{bill.amount} {bill.currency}"))
    return rows


def _money(value) -> str:
    return str(Decimal(value).quantize(CENT))
