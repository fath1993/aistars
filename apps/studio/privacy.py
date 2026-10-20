"""
What users leave behind, and how it goes.

Deleting a chat removes its messages and memory, its words from the requests
(Job.prompt and the photos and files sent; the amounts stay for statements),
from the safety checks it passed (a blocked one stays: the record of a
strike), and its copies from the archive channel where Telegram allows.
Chats nobody used for CHAT_KEEP_DAYS go the same way every night, and so do
the words of older requests outside chats (image descriptions, texts read
aloud). Stated on /privacy.
"""

from datetime import timedelta

from django.utils import timezone

from apps.core import runtime
from apps.safety.models import Result, Screening

from . import archive
from .models import Conversation, Job


def delete_chat(conversation):
    _forget(Job.objects.filter(conversation=conversation))
    conversation.delete()


def delete_all(account) -> int:
    chats = list(Conversation.objects.filter(account=account))
    for conversation in chats:
        delete_chat(conversation)
    return len(chats)


def _forget(jobs):
    """The words, files and archive copies of these requests; what they cost stays."""
    rows = list(jobs.values_list("pk", "screening_id", "archive_ids", "archive_message_id"))
    if not rows:
        return
    archive.remove([pk for _job, _check, ids, _out in rows for pk in ids or []]
                   + [out for _job, _check, _ids, out in rows if out])
    Screening.objects.filter(pk__in=[check for _job, check, _ids, _out in rows if check],
                             result__in=(Result.PASSED, Result.UNAVAILABLE)).update(
        prompt="", photos=[], photo_file_id="")
    Job.objects.filter(pk__in=[job for job, _check, _ids, _out in rows]).update(
        prompt="", photos=[], archive_ids=[], archive_message_id=None)


def purge() -> int:
    """Every night: chats unused for CHAT_KEEP_DAYS, and older requests' words."""
    days = int(runtime.get("CHAT_KEEP_DAYS"))
    if not days:
        return 0
    before = timezone.now() - timedelta(days=days)
    old = list(Conversation.objects.filter(updated_at__lt=before))
    for conversation in old:
        delete_chat(conversation)
    _forget(Job.objects.filter(created_at__lt=before).exclude(prompt="", photos=[]))
    # Checks of requests never made (cancelled, refused for credits).
    Screening.objects.filter(created_at__lt=before,
                             result__in=(Result.PASSED, Result.UNAVAILABLE)).exclude(
        prompt="").update(prompt="", photos=[], photo_file_id="")
    return len(old)
