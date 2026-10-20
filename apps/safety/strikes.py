"""
Strikes: every blocked request counts one; at the limit (Runtime setting
"Strikes before blocking") the account is blocked by itself. A block the
owner overturns in the review queue takes its strike back.
"""

from django.db.models import F
from django.utils import timezone

from apps.core import activity, runtime
from apps.core.models import Account, AccountStatus, LogCategory
from apps.core.services import telegram

from .models import Review


def limit() -> int:
    return int(runtime.get("SAFETY_STRIKE_LIMIT") or 3)


def add(account, screening) -> bool:
    """One strike; True if it blocked the account. `account` is updated."""
    Account.objects.filter(pk=account.pk).update(strikes=F("strikes") + 1)
    account.strikes = Account.objects.values_list("strikes", flat=True).get(pk=account.pk)
    blocked_now = account.strikes >= limit() and not account.is_blocked
    if blocked_now:
        Account.objects.filter(pk=account.pk).update(status=AccountStatus.BLOCKED)
        account.status = AccountStatus.BLOCKED
        activity.warn(LogCategory.SAFETY, "auto_blocked",
                      f"{account} blocked by itself after {account.strikes} strikes",
                      account=account, detail={"screening": screening.pk})
    _archive(screening, account)
    _tell_owner(screening, account, blocked_now)
    return blocked_now


def confirm(screening, by):
    screening.review, screening.reviewed_by, screening.reviewed_at = (
        Review.CONFIRMED, by, timezone.now())
    screening.save(update_fields=["review", "reviewed_by", "reviewed_at"])


def overturn(screening, by) -> Account:
    """Not a violation: the strike is taken back (the account is not unblocked)."""
    if screening.review != Review.OVERTURNED and screening.strike:
        Account.objects.filter(pk=screening.account_id, strikes__gt=0).update(
            strikes=F("strikes") - 1)
    screening.review, screening.reviewed_by, screening.reviewed_at = (
        Review.OVERTURNED, by, timezone.now())
    screening.save(update_fields=["review", "reviewed_by", "reviewed_at"])
    activity.info(LogCategory.SAFETY, "block_overturned",
                  f"#S{screening.pk} overturned by {by}: strike taken back",
                  account=screening.account)
    return Account.objects.get(pk=screening.account_id)


def _archive(screening, account):
    """The blocked request in the archive channel: what was asked, and the photos."""
    from apps.studio import archive

    chat = archive.chat_id()
    if not chat:
        return
    label = f"#S{screening.pk} · user {account.telegram_user_id}"
    text = (f"🚫 {label} · blocked by {screening.get_layer_display()}: "
            f"{screening.get_category_display()}\n\n{telegram.esc(screening.prompt)}")
    sent = telegram.send_message(chat, text)
    if sent:
        screening.archive_message_id = sent["message_id"]
        screening.save(update_fields=["archive_message_id"])
    photos = screening.photos or ([{"file_id": screening.photo_file_id,
                                     "kind": screening.photo_kind}]
                                   if screening.photo_file_id else [])
    for photo in photos:
        telegram.send_media(chat, photo.get("kind") or "photo", photo["file_id"],
                            caption=f"{label} · blocked input", silent=True)


def _tell_owner(screening, account, blocked_now):
    """In English: the admin speaks English."""
    owner = runtime.get("ADMIN_CHAT_ID")
    if not owner:
        return
    who = account.display_name or account.username or account.telegram_user_id
    lines = ["🚫 <b>Request blocked</b>",
             f"From: {telegram.esc(who)} ({telegram.esc(account.country or '—')})",
             f"By: {screening.get_layer_display()} · {screening.get_category_display()}",
             f"Strikes: {account.strikes} of {limit()}"
             + (" — <b>the account is now blocked</b>" if blocked_now else "")]
    base = str(runtime.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if base:
        lines.append(f"{base}/admin/safety/reviewitem/{screening.pk}/change/")
    telegram.send_message(owner, "\n".join(lines))
