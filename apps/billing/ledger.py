"""
Writing to the credit ledger. The only way credits move.

Every write locks the account's row first, so two quick payments, or a
payment and a refund, can never both read the same old balance.
"""

from django.db import transaction

from .models import LedgerEntry


class InsufficientCredits(Exception):
    """The movement would take the balance below zero."""


def balance(account) -> int:
    """The balance after the account's newest ledger row (0 without any)."""
    return (LedgerEntry.objects.filter(account=account).order_by("-id")
            .values_list("balance_after", flat=True).first()) or 0


def post(account, kind, amount, *, note="", created_by="system", payment=None,
         request=None, job=None) -> LedgerEntry:
    """Add one movement; raises InsufficientCredits rather than going negative."""
    from apps.core.models import Account

    with transaction.atomic():
        Account.objects.select_for_update().filter(pk=account.pk).first()
        new_balance = balance(account) + amount
        if new_balance < 0:
            raise InsufficientCredits(
                f"{account} has {new_balance - amount} credits, {-amount} needed")
        return LedgerEntry.objects.create(
            account=account, kind=kind, amount=amount, balance_after=new_balance,
            note=note[:255], created_by=created_by, payment=payment, request=request,
            job=job)


def history(account, page=0, per_page=10):
    """
    (entries newest first, has_older) for one page of the statement. A hold
    whose request is settled is left out: the settlement's line shows what the
    request cost, so each request reads as one line.
    """
    from .models import HOLD_KINDS, EntryKind

    settled = LedgerEntry.objects.filter(
        account=account, kind__in=HOLD_KINDS[1:]).values("job_id")
    rows = (LedgerEntry.objects.filter(account=account)
            .exclude(kind=EntryKind.HOLD, job_id__in=settled)
            .select_related("job").order_by("-id"))
    entries = list(rows[page * per_page:(page + 1) * per_page + 1])
    return entries[:per_page], len(entries) > per_page
