"""
What happens to money: payments, refunds, credit requests and adjustments.
Every credit movement goes through ledger.post.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core import activity
from apps.core.models import LogCategory

from . import ledger
from .models import (
    CreditRequest,
    EntryKind,
    LedgerEntry,
    Pack,
    Payment,
    PaymentStatus,
    RequestStatus,
)
from .pricing import icon

MAX_REQUEST = 100_000


# --------------------------------------------------------------------------
# Stars payments
# --------------------------------------------------------------------------

def invoice_payload(pack) -> str:
    """What the invoice promised; read back when the payment arrives."""
    return f"pack:{pack.pk}:{pack.stars}:{pack.credits}"


def parse_payload(payload):
    """(pack_id, stars, credits) or None."""
    parts = str(payload or "").split(":")
    if len(parts) != 4 or parts[0] != "pack" or not all(p.isdigit() for p in parts[1:]):
        return None
    return tuple(int(part) for part in parts[1:])


def pre_checkout_problem(account, payload, currency, total) -> str:
    """'' when Telegram may take the payment, else what is wrong (for the log)."""
    if account.is_blocked or not account.is_registered or not account.region_allowed:
        return "account"
    parsed = parse_payload(payload)
    if parsed is None or currency != "XTR":
        return "payload"
    pack_id, stars, credits = parsed
    pack = Pack.objects.filter(pk=pack_id, active=True).first()
    if pack is None or (pack.stars, pack.credits) != (stars, credits) or total != stars:
        return "pack"
    return ""


def record_payment(account, successful_payment):
    """
    Credit a payment once. Returns (payment, created). Telegram may deliver
    the same payment twice; the charge ID makes the second a no-op.
    """
    charge_id = successful_payment["telegram_payment_charge_id"]
    existing = Payment.objects.filter(telegram_charge_id=charge_id).first()
    if existing:
        return existing, False

    stars = int(successful_payment.get("total_amount", 0))
    parsed = parse_payload(successful_payment.get("invoice_payload"))
    # Paid is paid: an unreadable payload still credits what was paid.
    pack_id, credits = (parsed[0], parsed[2]) if parsed else (None, stars)
    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                account=account, pack=Pack.objects.filter(pk=pack_id).first(),
                stars=stars, credits=credits, telegram_charge_id=charge_id,
                provider_charge_id=successful_payment.get("provider_payment_charge_id", ""),
                payload=str(successful_payment.get("invoice_payload", ""))[:128])
            ledger.post(account, EntryKind.PURCHASE, credits, payment=payment)
    except IntegrityError:   # the same payment, delivered twice at once
        return Payment.objects.get(telegram_charge_id=charge_id), False
    activity.info(LogCategory.BILLING, "payment",
                  f"{account} paid ⭐ {stars} for {icon()} {credits}", account=account,
                  detail={"charge_id": charge_id, "stars": stars, "credits": credits})
    return payment, True


def refund(payment, by) -> tuple:
    """
    (ok, message). Refused when the user has already spent the credits:
    Telegram returns the Stars, so the credits must still be there to remove.
    """
    from apps.core.services import telegram

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status != PaymentStatus.PAID:
            return False, f"{payment}: already refunded."
        balance = ledger.balance(payment.account)
        if balance < payment.credits:
            return False, (f"{payment}: the user has {icon()} {balance} left of the {icon()} "
                           f"{payment.credits} bought, so it cannot be refunded.")
        if not telegram.refund_star_payment(payment.account.telegram_user_id,
                                            payment.telegram_charge_id):
            return False, f"{payment}: Telegram refused the refund (see the log)."
        ledger.post(payment.account, EntryKind.REFUND, -payment.credits,
                    payment=payment, created_by=by, note=f"refund of {payment.telegram_charge_id}")
        payment.status = PaymentStatus.REFUNDED
        payment.refunded_at = timezone.now()
        payment.refunded_by = by
        payment.save(update_fields=["status", "refunded_at", "refunded_by"])
    activity.info(LogCategory.BILLING, "refund", f"{payment} refunded by {by}",
                  account=payment.account)
    return True, f"Refunded ⭐ {payment.stars} to {payment.account}."


# --------------------------------------------------------------------------
# Credit requests and adjustments
# --------------------------------------------------------------------------

def has_pending_request(account) -> bool:
    return CreditRequest.objects.filter(account=account,
                                        status=RequestStatus.PENDING).exists()


def create_request(account, amount, note) -> CreditRequest:
    request = CreditRequest.objects.create(account=account, amount=amount, note=note[:2000])
    activity.info(LogCategory.BILLING, "credit_request",
                  f"{account} asked for {icon()} {amount}", account=account)
    return request


def approve(request, amount, by) -> int:
    """Grant `amount` (any amount); returns the new balance."""
    with transaction.atomic():
        request = CreditRequest.objects.select_for_update().get(pk=request.pk)
        if request.status != RequestStatus.PENDING:
            raise ValueError(f"{request} is already decided")
        entry = ledger.post(request.account, EntryKind.MANUAL_GRANT, amount,
                            request=request, created_by=by)
        request.status, request.granted = RequestStatus.APPROVED, amount
        request.decided_by, request.decided_at = by, timezone.now()
        request.save()
    activity.info(LogCategory.BILLING, "credit_request_approved",
                  f"{icon()} {amount} granted to {request.account} by {by}",
                  account=request.account)
    return entry.balance_after


def reject(request, by):
    with transaction.atomic():
        request = CreditRequest.objects.select_for_update().get(pk=request.pk)
        if request.status != RequestStatus.PENDING:
            raise ValueError(f"{request} is already decided")
        request.status = RequestStatus.REJECTED
        request.decided_by, request.decided_at = by, timezone.now()
        request.save()
    activity.info(LogCategory.BILLING, "credit_request_rejected",
                  f"request of {request.account} rejected by {by}", account=request.account)


def adjust(account, amount, reason, by) -> int:
    """An admin correction, + or −; returns the new balance."""
    entry = ledger.post(account, EntryKind.ADMIN_ADJUST, amount, note=reason, created_by=by)
    activity.info(LogCategory.BILLING, "credit_adjusted",
                  f"{account}: {amount:+d} by {by} ({reason})", account=account)
    return entry.balance_after


# --------------------------------------------------------------------------
# Requests: the most they can cost is held when they start, what they really
# cost is charged when they are done, everything goes back when they fail
# --------------------------------------------------------------------------

def hold(job):
    """
    Take the most the request can cost out of the balance. Raises
    InsufficientCredits. A free request (hold 0) moves nothing and leaves no row.
    """
    if job.hold <= 0:
        return None
    return ledger.post(job.account, EntryKind.HOLD, -job.hold, job=job)


def charge(job, credits, by="system"):
    """Done: `credits` are charged (never more than the hold); the rest goes back."""
    charged = min(max(int(credits), 0), job.hold)
    return _settle(job, EntryKind.CHARGE, job.hold - charged, by)


def give_back(job, by="system"):
    """Not done: the whole hold goes back to the balance."""
    return _settle(job, EntryKind.RETURN, job.hold, by)


def _settle(job, kind, amount, by):
    """
    Once per request, and only after a hold: a charge or a return, never
    both. The account's row is locked first, so a result and a failure
    arriving together cannot both settle.
    """
    from apps.core.models import Account

    with transaction.atomic():
        Account.objects.select_for_update().filter(pk=job.account_id).first()
        kinds = set(LedgerEntry.objects.filter(job=job).values_list("kind", flat=True))
        if EntryKind.HOLD not in kinds or kinds & {EntryKind.CHARGE, EntryKind.RETURN}:
            return None
        return ledger.post(job.account, kind, amount, job=job, created_by=by)


def welcome(account) -> int:
    """
    The credits a new user gets on registering (Runtime setting "Credits for a
    new user"): once per account, and once per phone number, so a second
    Telegram account on the same number gets none. Returns how many.
    """
    from apps.core import runtime

    amount = int(runtime.get("WELCOME_CREDITS"))
    if amount <= 0:
        return 0
    given = LedgerEntry.objects.filter(kind=EntryKind.WELCOME)
    if given.filter(account=account).exists() or (
            account.phone and given.filter(account__phone=account.phone).exists()):
        return 0
    ledger.post(account, EntryKind.WELCOME, amount, note="welcome credits")
    activity.info(LogCategory.BILLING, "welcome_credits",
                  f"{account} got {icon()} {amount} on registering", account=account)
    return amount
