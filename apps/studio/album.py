"""
Photos and files in, as the user sent them: one message, however many files
it carries.

Telegram hands a bot an album (up to 10 photos or files sent as one
message) as separate updates sharing a media_group_id, and never says when
the last one has come. So each piece is kept (AlbumItem), and a check is
queued for QUIET seconds after it; the check that finds no newer piece
sends them all on together. A message with one file goes on at once. The
work runs in the background: a PDF is read for its pages, so its hold
covers them.
"""

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.core import languages
from apps.core.i18n import gettext as _
from apps.core.models import Account

from . import files, state
from .models import AlbumItem

QUIET = 1.5


def add(account, chat_id, message):
    """Keep the message's files, then send them on: now, or once its album is complete."""
    album = message.get("media_group_id")
    group = f"{account.pk}:{album or 'm' + str(message['message_id'])}"
    for item in files.of_message(message):
        AlbumItem.objects.create(account=account, group=group, chat_id=chat_id,
                                 message_id=message["message_id"],
                                 caption=(message.get("caption") or "").strip(), item=item)
    from . import tasks

    if album:
        tasks.flush_album.apply_async((account.pk, group), countdown=QUIET)
    else:
        tasks.flush_album.delay(account.pk, group, 0)


def flush(account_id, group, quiet=QUIET) -> str:
    """Send the pieces on, once: only when no piece came in the last `quiet` seconds."""
    rows = AlbumItem.objects.filter(account_id=account_id, group=group)
    newest = rows.order_by("-created_at").values_list("created_at", flat=True).first()
    if newest is None:
        return "gone"
    if (timezone.now() - newest).total_seconds() < quiet * 0.8:
        return "waiting"   # a newer piece came: its own check sends them
    if not cache.add(f"album:{group}", 1, 600):
        return "taken"
    pieces = list(rows)
    AlbumItem.objects.filter(pk__in=[piece.pk for piece in pieces]).delete()
    account = Account.objects.select_related("country").get(pk=account_id)
    caption = next((piece.caption for piece in pieces if piece.caption), "")
    with languages.activate_for(account):
        deliver(account, pieces[0].chat_id, [piece.item for piece in pieces], caption)
    return "sent"


def deliver(account, chat_id, items, caption):
    """Where the files go: an edit waiting for photos, voice to text, or the chat."""
    from . import chat, images, voice

    draft = state.waiting(account) or {}
    step = draft.get("step")
    audio = [item for item in items if item["kind"] in ("voice", "audio")]
    others = [item for item in items if item["kind"] not in ("voice", "audio")]
    photos = [item["file_id"] for item in others if item["kind"] == "photo"]
    if audio:
        voice.start_transcription(account, chat_id, audio, then_chat=step != "transcribe",
                                  attachments=others)
        return
    if photos and step in ("edit_photo", "edit_prompt", "image_summary"):
        images.got_photos(account, chat_id, photos,
                          {**draft, "prompt": caption} if caption else draft)
        return
    state.stop(account)
    for item in others:
        files.count_pages(item)
    chat.send(account, chat_id, caption or default_question(others), attachments=others)


def default_question(items) -> str:
    """Files sent without a question: the user's language, so the answer is in it."""
    if all(item["kind"] == "photo" for item in items):
        return _("What is in this photo?") if len(items) == 1 else _(
            "What is in these photos?")
    return _("Summarize this file.") if len(items) == 1 else _("Summarize these files.")


def forget_stale() -> int:
    """Pieces whose check never ran (a worker restarted): gone after an hour."""
    deleted, _by_model = AlbumItem.objects.filter(
        created_at__lt=timezone.now() - timedelta(hours=1)).delete()
    return deleted
