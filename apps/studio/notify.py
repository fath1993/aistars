"""Telling the user a request was not done (its credits are already back)."""

from apps.core.services import telegram

from . import texts
from .models import Kind


def failed(job):
    if job.kind == Kind.CHAT:
        text, markup = texts.answer_failed_text(job), texts.retry_keyboard(job)
    elif job.kind in (Kind.IMAGE, Kind.EDIT):
        text, markup = texts.image_failed_text(job), texts.again_keyboard(job)
    else:
        text, markup = texts.voice_failed_text(job), texts.notice_keyboard()
    # Written into the message the answer was going to fill, when there is one.
    if job.message_id and telegram.edit_message(job.chat_id, job.message_id, text, markup):
        return
    telegram.send_message(job.chat_id, text, reply_markup=markup)
