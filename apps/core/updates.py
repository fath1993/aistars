"""
The webhook's record of every update (TelegramUpdate): stored as it came,
handled once however often Telegram sends it, marked handled or failed with
the error, and kept for TELEGRAM_UPDATES_KEEP_DAYS. Updates with a wrong
secret are recorded too, at most one a minute, so "Telegram never sent it"
and "we turned it away" can be told apart.
"""

from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from . import runtime
from .models import TelegramUpdate, UpdateOutcome

PREVIEW = 200
# What a rejected body may weigh: anyone can post to the webhook.
REJECTED_MAX_BYTES = 20_000


def receive(update):
    """
    The row for a new update, or None when this update was received before
    (Telegram sending it again): the caller then does nothing.
    """
    kind, user_id, summary = describe(update)
    update_id = update.get("update_id")
    try:
        with transaction.atomic():
            return TelegramUpdate.objects.create(
                update_id=update_id if isinstance(update_id, int) else None, kind=kind,
                user_id=user_id, summary=summary, payload=update)
    except IntegrityError:
        TelegramUpdate.objects.filter(update_id=update_id).update(
            deliveries=F("deliveries") + 1)
        return None


def handled(row):
    _finish(row, UpdateOutcome.HANDLED)


def failed(row, exc):
    _finish(row, UpdateOutcome.FAILED, error_text(exc))


def _finish(row, outcome, error=""):
    TelegramUpdate.objects.filter(pk=row.pk).update(outcome=outcome, error=error,
                                                    handled_at=timezone.now())


def error_text(exc) -> str:
    """The error, without the bot token (a URL to the Bot API carries it)."""
    text = f"{type(exc).__name__}: {exc}"
    token = str(runtime.get("TELEGRAM_BOT_TOKEN") or "")
    return (text.replace(token, "<token>") if token else text)[:2000]


def rejected(body):
    """A delivery with a wrong secret: recorded at most once a minute."""
    if not cache.add("updates:rejected", 1, 60):
        return
    import json

    try:
        payload = json.loads(body[:REJECTED_MAX_BYTES]) if len(body) <= REJECTED_MAX_BYTES \
            else {"note": f"a body of {len(body)} bytes, not kept"}
    except ValueError:
        payload = {"note": "not JSON"}
    if not isinstance(payload, dict):
        payload = {"body": payload}
    kind, user_id, summary = describe(payload)
    TelegramUpdate.objects.create(kind=kind, user_id=user_id, summary=summary,
                                  payload=payload, outcome=UpdateOutcome.REJECTED,
                                  handled_at=timezone.now())


def describe(update) -> tuple:
    """(kind, the user or chat it came from, a short preview) of an update."""
    kind = next((key for key in update if key != "update_id"), "")
    body = update.get(kind) if isinstance(update.get(kind), dict) else {}
    sender = body.get("from") or body.get("chat") or (body.get("message") or {}).get("chat") \
        or {}
    user_id = sender.get("id") if isinstance(sender.get("id"), int) else None
    return kind, user_id, _preview(kind, body)[:PREVIEW]


def _preview(kind, body) -> str:
    if kind == "callback_query":
        return f"button {body.get('data', '')}"
    if kind == "pre_checkout_query":
        return f"payment check: {body.get('total_amount', '')} {body.get('currency', '')}"
    if kind in ("my_chat_member", "chat_member"):
        chat = body.get("chat") or {}
        old = (body.get("old_chat_member") or {}).get("status", "")
        new = (body.get("new_chat_member") or {}).get("status", "")
        return f"{chat.get('type', '')} “{chat.get('title') or chat.get('id', '')}”: {old} → {new}"
    return _message_preview(body)


def _message_preview(message) -> str:
    if message.get("text"):
        return message["text"]
    if message.get("successful_payment"):
        payment = message["successful_payment"]
        return f"paid {payment.get('total_amount', '')} {payment.get('currency', '')}"
    if message.get("contact"):
        return "shared a phone number"
    for key, label in (("photo", "photo"), ("document", "file"), ("voice", "voice note"),
                       ("audio", "audio"), ("video", "video"), ("video_note", "video note"),
                       ("animation", "GIF"), ("sticker", "sticker")):
        if message.get(key):
            item = message[key] if isinstance(message[key], dict) else {}
            name = item.get("file_name") or ""
            caption = message.get("caption") or ""
            return " ".join(part for part in (f"[{label}{': ' + name if name else ''}]",
                                              caption) if part)
    return ""


def purge() -> int:
    """Updates older than TELEGRAM_UPDATES_KEEP_DAYS (0: kept forever)."""
    days = int(runtime.get("TELEGRAM_UPDATES_KEEP_DAYS"))
    if not days:
        return 0
    deleted, _rows = TelegramUpdate.objects.filter(
        received_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted
