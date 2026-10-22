"""
The archive channel: a private Telegram channel where every photo and voice
note sent in, and every image and voice note made, is copied, captioned
"#J12 · user 51234 · input". It is how the owner sees what was asked and
made, and the files never live on this server. Chat texts are not copied:
they are kept in the database, with the chat.

Setting it: Control panel → Archive channel lists the channels the bot has
heard from (it is admin there and something was posted, or it was just made
admin), and the owner picks one; the bot posts a line there to be sure it
may. It is also recorded by itself when the owner (Runtime setting "Your
Telegram ID") makes the bot an admin of a channel; anyone else doing so is
ignored, and is only listed on that page.
"""

from datetime import timedelta

from django.utils import timezone

from apps.core import activity, runtime
from apps.core.models import LogCategory, RuntimeSetting, TelegramUpdate
from apps.core.services import telegram


def chat_id():
    return runtime.get("ARCHIVE_CHAT_ID") or None


def _set(value):
    row, _ = RuntimeSetting.objects.get_or_create(
        key="ARCHIVE_CHAT_ID", defaults={"group": "studio", "label": "Archive channel",
                                         "kind": "int"})
    row.value = str(value or "")
    row.save()


def _tell_owner(owner, text):
    """In English: the admin speaks English."""
    if owner:
        telegram.send_message(owner, text)


def on_member_update(update):
    """
    Telegram's my_chat_member: the bot's own membership changed somewhere.
    Only channels matter; the bot never joins groups on purpose.
    """
    chat = update.get("chat") or {}
    if chat.get("type") != "channel":
        return
    owner = runtime.get("ADMIN_CHAT_ID")
    member = update.get("new_chat_member") or {}
    status = member.get("status")
    by = (update.get("from") or {}).get("id")
    title = telegram.esc(chat.get("title") or chat.get("id"))

    if status == "administrator":
        if not owner or by != owner:
            activity.warn(LogCategory.STUDIO, "archive_refused",
                          f"bot made admin of channel {chat.get('id')} by {by}, who is not "
                          f"the owner (Your Telegram ID: {owner or 'not set'})",
                          detail={"chat": chat.get("id"), "by": by})
            return
        if not member.get("can_post_messages", True):
            _tell_owner(owner, f"⚠️ The bot is an admin of <b>{title}</b> but may not post "
                               f"there. Allow it to post messages to use it as the archive.")
            return
        _set(chat["id"])
        activity.info(LogCategory.STUDIO, "archive_set",
                      f"archive channel set to {chat['id']} ({chat.get('title', '')})",
                      detail={"chat": chat["id"]})
        _tell_owner(owner, f"✅ <b>{title}</b> is now the archive channel. Every photo and "
                           f"voice note sent in, and every image and voice note made, is "
                           f"copied there.")
        return

    if status in ("left", "kicked", "member", "restricted") and chat.get("id") == chat_id():
        _set("")
        activity.error(LogCategory.STUDIO, "archive_lost",
                       f"the bot is no longer an admin of the archive channel {chat['id']}; "
                       f"nothing is archived now", detail={"chat": chat["id"], "status": status})
        _tell_owner(owner, f"⚠️ The bot is no longer an admin of <b>{title}</b>, so nothing "
                           f"is archived. Make it an admin of a channel again.")


# --------------------------------------------------------------------------
# Choosing it in the admin
# --------------------------------------------------------------------------

def seen_channels(days=30, limit=20) -> list:
    """
    The channels the bot has heard from lately, newest first: [{"id", "title",
    "seen", "status"}]. From the Telegram updates kept: a post there, or the
    bot being made admin (or removed).
    """
    since = timezone.now() - timedelta(days=days)
    found = {}
    rows = TelegramUpdate.objects.filter(
        kind__in=("channel_post", "my_chat_member"), received_at__gte=since).order_by(
        "-received_at")[:1000]
    for row in rows:
        body = row.payload.get(row.kind) or {}
        chat = body.get("chat") or {}
        if chat.get("type") != "channel" or chat.get("id") in found:
            continue
        status = ((body.get("new_chat_member") or {}).get("status")
                  if row.kind == "my_chat_member" else "posting")
        found[chat["id"]] = {"id": chat["id"], "title": chat.get("title") or "",
                             "seen": row.received_at, "status": status}
        if len(found) == limit:
            break
    return list(found.values())


def connect(reference, by="") -> tuple:
    """
    Use a channel (its ID, or @username) as the archive, once the bot has
    posted a line in it: (ok, what to tell the owner).
    """
    reference = str(reference).strip()
    if not reference:
        return False, "Type the channel's ID or @username."
    chat = telegram.get_chat(int(reference) if reference.lstrip("-").isdigit() else reference)
    if not isinstance(chat, dict) or chat.get("type") != "channel":
        return False, ("Telegram does not know this channel, or the bot is not in it. Make the "
                       "bot an admin of the channel, then try again.")
    title = chat.get("title") or str(chat["id"])
    sent = telegram.send_message(chat["id"], "✅ This channel is now the AIstars archive.",
                                 parse_mode=None)
    if not sent:
        return False, (f"The bot may not post in “{title}”: make it an admin with the Post "
                       "messages right, then try again.")
    _set(chat["id"])
    activity.info(LogCategory.STUDIO, "archive_set",
                  f"archive channel set to {chat['id']} ({title}) in the admin by {by}",
                  detail={"chat": chat["id"]})
    return True, f"“{title}” ({chat['id']}) is now the archive channel."


def disconnect(by=""):
    _set("")
    activity.warn(LogCategory.STUDIO, "archive_cleared",
                  f"archive channel cleared in the admin by {by}: nothing is archived now")


def caption(job, role) -> str:
    return f"#J{job.pk} · user {job.account.telegram_user_id} · {role}"


def copy_input(job, kind, file_id):
    """A photo or voice note the user sent, copied by file_id (no download)."""
    archive = chat_id()
    if not archive:
        return None
    sent = telegram.send_media(archive, kind, file_id, caption=caption(job, "input"),
                               silent=True)
    if not sent:
        activity.warn(LogCategory.STUDIO, "archive_failed",
                      f"could not copy the input of #{job.pk} to the archive",
                      account=job.account)
    return sent


def store_output(job, kind, data, filename, content_type, role="output"):
    """
    Upload something made to the archive. Returns the sent message, or None
    when there is no archive or Telegram refused (the caller then uploads to
    the user directly).
    """
    archive = chat_id()
    if not archive:
        return None
    sent = telegram.send_media(archive, kind, data, caption=caption(job, role),
                               filename=filename, content_type=content_type, silent=True)
    if not sent:
        activity.warn(LogCategory.STUDIO, "archive_failed",
                      f"could not upload the result of #{job.pk} to the archive",
                      account=job.account)
    return sent
