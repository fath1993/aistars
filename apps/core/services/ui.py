"""
Chat surface management.

Navigation lives on ONE panel message per chat that gets edited in place;
transient notices delete themselves; only deliverables persist (AI replies,
images, voice notes). A bot that appends a message per tap turns into a wall
of dead menus.
"""

import logging

from . import telegram

logger = logging.getLogger(__name__)


def screen(account, chat_id, text, markup=None):
    """
    Show `text` on the panel: edit the existing one if there is one,
    otherwise send a new message and remember it.

    A failed edit falls back to sending, rather than showing nothing.
    """
    if account.ui_message_id:
        edited = telegram.edit_message(chat_id, account.ui_message_id, text, markup)
        if edited:
            return account.ui_message_id
        logger.info("could not edit panel %s, sending fresh", account.ui_message_id)

    return _send_panel(account, chat_id, text, markup)


def fresh_screen(account, chat_id, text, markup=None):
    """
    Show the panel as a NEW message at the bottom of the chat, removing the
    old one. For /start and /menu, and for a screen opened from a reply:
    editing a panel far up the chat would leave the user looking at nothing.
    """
    clear_screen(account, chat_id)
    return _send_panel(account, chat_id, text, markup)


def clear_screen(account, chat_id):
    """Remove the panel, so the next screen is drawn at the bottom of the chat."""
    if account.ui_message_id:
        telegram.delete_message(chat_id, account.ui_message_id)
        forget_screen(account)


def _send_panel(account, chat_id, text, markup):
    sent = telegram.send_message(chat_id, text, reply_markup=markup)
    if not sent:
        return None
    _remember(account, sent["message_id"])
    return sent["message_id"]


def _remember(account, message_id):
    from ..models import Account

    Account.objects.filter(pk=account.pk).update(ui_message_id=message_id)
    account.ui_message_id = message_id


def notice(chat_id, text):
    """A short-lived acknowledgement. Returns its id so it can be cleared."""
    sent = telegram.send_message(chat_id, text)
    return sent["message_id"] if sent else None


def clear(chat_id, message_id):
    if message_id:
        telegram.delete_message(chat_id, message_id)


def forget_screen(account):
    """Called when the panel message is gone or consumed by something else."""
    from ..models import Account

    Account.objects.filter(pk=account.pk).update(ui_message_id=None)
    account.ui_message_id = None


def adopt(account, message_id):
    """The message the user tapped becomes the panel that gets edited."""
    from ..models import Account

    if message_id and account.ui_message_id != message_id:
        Account.objects.filter(pk=account.pk).update(ui_message_id=message_id)
        account.ui_message_id = message_id


def drop_user_message(chat_id, message_id):
    """Remove the user's own tap from the transcript (allowed in private chats)."""
    telegram.delete_message(chat_id, message_id)
