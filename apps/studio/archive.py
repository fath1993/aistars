"""
The archive channel: a private Telegram channel where every photo and voice
note sent in, and every image and voice note made, is copied, captioned
"#J12 · user 51234 · input". It is how the owner sees what was asked and
made, and the files never live on this server. Chat texts are not copied:
they are kept in the database, with the chat.

The channel is recorded by itself: the owner makes the bot an admin of it.
Only the owner (Runtime setting "Your Telegram ID") can do that; anyone else
adding the bot to a channel is ignored.
"""

from apps.core import activity, runtime
from apps.core.models import LogCategory, RuntimeSetting
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
