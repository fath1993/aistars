"""
Telegram Bot API calls. Every call goes through _call(), which never raises:
a failed send returns None and is logged.
"""

import html
import json
import logging

import requests
from django.conf import settings

from .. import runtime

logger = logging.getLogger(__name__)

TIMEOUT = 15

# Telegram rejects messages over 4096 characters outright.
MAX_MESSAGE = 4096


def _call(method: str, files=None, timeout=TIMEOUT, **payload):
    """`files` uploads as multipart: {"video": ("name.mp4", bytes, "video/mp4")}."""
    token = runtime.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("telegram.%s skipped: no bot token configured", method)
        # Otherwise every reply silently does nothing and nothing in the admin
        # says why. Throttled, because one inbound message triggers several
        # sends.
        from django.core.cache import cache

        if cache.add("activity:throttle:no_bot_token", 1, 300):
            from .. import activity
            from ..models import LogCategory

            activity.error(
                LogCategory.SYSTEM, "no_bot_token",
                "TELEGRAM_BOT_TOKEN is not set, so the bot cannot reply to "
                "anything. Set it at /admin/core/runtimesetting/.",
            )
        return None

    url = f"{settings.TELEGRAM_API_BASE}/bot{token}/{method}"
    try:
        response = requests.post(url, data=payload, files=files, timeout=timeout)
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        # The exception's own text quotes the URL, and the URL holds the bot
        # token: only its type is logged (Deployment standard 13).
        logger.error("telegram.%s failed: %s", method, type(exc).__name__)
        return None

    if not body.get("ok"):
        description = str(body.get("description", ""))
        # Expected in a UI that edits one message: stale buttons, identical
        # edits, deleting something already gone. Logged quietly so the real
        # errors still stand out.
        quiet = ("query is too old", "query ID is invalid",
                 "message is not modified", "message to delete not found",
                 "message to edit not found", "message can't be deleted",
                 # A panel button under a video: the panel moves to a new message.
                 "there is no text in the message to edit")
        if any(marker in description for marker in quiet):
            logger.info("telegram.%s: %s", method, description)
        else:
            logger.error("telegram.%s error: %s", method, description)
        return None
    return body["result"]


def esc(value) -> str:
    """
    Escape anything that came from a user before it goes into an HTML-parsed
    message. Telegram rejects the whole message on a stray '<' or bare '&'.
    """
    return html.escape(str(value), quote=False)


def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    if len(text) > MAX_MESSAGE:
        # Never silently slice HTML mid-tag; long content goes through
        # send_blocks().
        logger.error(
            "refusing to send a %s-char message to %s; use send_blocks()",
            len(text), chat_id,
        )
        text = text[:MAX_MESSAGE - 40].rsplit("\n", 1)[0] + "\n\n[truncated]"

    return _call(
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        link_preview_options=json.dumps({"is_disabled": True}),
    )


def send_blocks(chat_id, blocks, reply_markup=None):
    """
    Send self-contained HTML blocks, packed into as few messages as fit under
    the 4096-character limit. Splitting between blocks keeps every tag
    balanced.
    """
    messages, current = [], []
    length = 0
    for block in blocks:
        block_len = len(block) + 2
        if current and length + block_len > MAX_MESSAGE - 64:
            messages.append("\n\n".join(current))
            current, length = [], 0
        current.append(block)
        length += block_len
    if current:
        messages.append("\n\n".join(current))

    result = None
    for index, body in enumerate(messages):
        last = index == len(messages) - 1
        result = send_message(
            chat_id, body, reply_markup=reply_markup if last else None
        )
    return result


def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    """Rewrite a message in place. Returns None if Telegram refused."""
    return _call(
        "editMessageText",
        chat_id=chat_id,
        message_id=message_id,
        text=text[:MAX_MESSAGE],
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        link_preview_options=json.dumps({"is_disabled": True}),
    )


def send_formatted(chat_id, html_text, plain_text, reply_markup=None):
    """
    An AI answer: as HTML, or as plain text if Telegram refuses the HTML (a
    tag the formatter got wrong must never lose the answer).
    """
    return (send_message(chat_id, html_text, reply_markup=reply_markup)
            or send_message(chat_id, plain_text, reply_markup=reply_markup, parse_mode=None))


def edit_formatted(chat_id, message_id, html_text, plain_text, reply_markup=None):
    """The same for an answer written into a message already sent."""
    return (edit_message(chat_id, message_id, html_text, reply_markup)
            or edit_message(chat_id, message_id, plain_text, reply_markup, parse_mode=None))


def send_chat_action(chat_id, action="typing"):
    """"typing…" (or "upload_photo", "record_voice") under the bot's name for 5 s."""
    return _call("sendChatAction", chat_id=chat_id, action=action)


def delete_message(chat_id, message_id):
    return _call("deleteMessage", chat_id=chat_id, message_id=message_id)


class Alert(str):
    """A button's answer shown as a box the user closes, not a passing toast."""


def answer_callback_query(callback_query_id, text=""):
    payload = {"callback_query_id": callback_query_id, "text": text[:200]}
    if isinstance(text, Alert):
        payload["show_alert"] = "true"
    return _call("answerCallbackQuery", **payload)


def clear_reply_markup(chat_id, message_id):
    if not chat_id or not message_id:
        return None
    return _call("editMessageReplyMarkup", chat_id=chat_id,
                 message_id=message_id, reply_markup="{}")


def bot_username() -> str:
    """
    The bot's own @name: the admin value if set, otherwise asked of Telegram.
    Never raises.
    """
    from django.core.cache import cache

    manual = str(runtime.get("TELEGRAM_BOT_USERNAME") or "").lstrip("@").strip()
    if manual:
        return manual

    cached = cache.get("core:bot-username")
    if cached is not None:
        return cached

    try:
        me = _call("getMe") or {}
        username = str(me.get("username") or "").lstrip("@")
    except Exception:
        username = ""

    # Cached either way, so a failed lookup does not retry on every read.
    cache.set("core:bot-username", username, 3600 if username else 120)
    return username


# pre_checkout_query: Telegram asks the bot to confirm every Stars payment
# before taking it, and cancels the payment if no answer comes in 10 seconds.
# my_chat_member: the bot was made admin of a channel (the archive channel).
ALLOWED_UPDATES = ["message", "callback_query", "pre_checkout_query", "my_chat_member"]


def set_webhook(url: str):
    """Register the webhook with the shared secret token."""
    return _call(
        "setWebhook",
        url=url,
        secret_token=runtime.get("TELEGRAM_WEBHOOK_SECRET"),
        allowed_updates=json.dumps(ALLOWED_UPDATES),
    )


def delete_my_commands(language_code=""):
    """Removes the list behind the ☰ Menu button (in one language, or the default)."""
    payload = {"language_code": language_code} if language_code else {}
    return _call("deleteMyCommands", **payload)


# --------------------------------------------------------------------------
# Telegram Stars
# --------------------------------------------------------------------------

def send_invoice(chat_id, title, description, payload, stars, label):
    """A Stars invoice: currency XTR, no provider token, one price line."""
    return _call(
        "sendInvoice",
        chat_id=chat_id,
        title=title[:32],
        description=description[:255],
        payload=payload,
        currency="XTR",
        prices=json.dumps([{"label": label, "amount": stars}]),
    )


def answer_pre_checkout_query(query_id, ok=True, error_message=""):
    payload = {"pre_checkout_query_id": query_id, "ok": "true" if ok else "false"}
    if not ok:
        payload["error_message"] = error_message[:200]
    return _call("answerPreCheckoutQuery", **payload)


def refund_star_payment(user_id, telegram_charge_id):
    return _call("refundStarPayment", user_id=user_id,
                 telegram_payment_charge_id=telegram_charge_id)


# --------------------------------------------------------------------------
# Files: photos and voice notes sent in, images and voice notes sent out
# --------------------------------------------------------------------------

# The Bot API's own limits: a bot downloads at most 20 MB and uploads at most
# 50 MB. Anything already in Telegram is re-sent by file_id, with no limit.
MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_UPLOAD = 50 * 1024 * 1024
UPLOAD_TIMEOUT = 120

# The method for each kind of file; the field it takes has the kind's name.
MEDIA_METHODS = {"video": "sendVideo", "photo": "sendPhoto", "document": "sendDocument",
                 "voice": "sendVoice", "audio": "sendAudio"}


def send_media(chat_id, kind, media, caption="", reply_markup=None, filename="",
               content_type="", silent=False):
    """
    Send a video, photo, document, voice note or audio file: `media` is a
    file_id (already in Telegram) or the bytes of a new file. Returns the sent
    message or None.
    """
    payload = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML",
               "reply_markup": reply_markup}
    if silent:
        payload["disable_notification"] = "true"
    if kind == "video":
        payload["supports_streaming"] = "true"
    if isinstance(media, (bytes, bytearray)):
        files = {kind: (filename or kind, bytes(media), content_type or None)}
        return _call(MEDIA_METHODS[kind], files=files, timeout=UPLOAD_TIMEOUT, **payload)
    return _call(MEDIA_METHODS[kind], **{kind: media}, **payload)


def file_id_of(message, kind):
    """The file_id Telegram gave a sent video, photo, document, voice or audio."""
    if not message:
        return ""
    if kind == "photo":
        sizes = message.get("photo") or [{}]
        return sizes[-1].get("file_id", "")
    return (message.get(kind) or {}).get("file_id", "")


def get_file(file_id):
    return _call("getFile", file_id=file_id)


def download_file(file_id) -> bytes:
    """
    The bytes of a file the user sent. Raises ValueError when Telegram will not
    give it (over 20 MB, gone, or the network failed). The URL holds the bot
    token, so neither it nor requests' own message (which quotes the URL) is
    ever passed on.
    """
    info = get_file(file_id)
    if not info or not info.get("file_path"):
        raise ValueError("Telegram did not give the file (over 20 MB, or gone)")
    if (info.get("file_size") or 0) > MAX_DOWNLOAD:
        raise ValueError("over Telegram's 20 MB download limit")
    token = runtime.get("TELEGRAM_BOT_TOKEN")
    url = f"{settings.TELEGRAM_API_BASE}/file/bot{token}/{info['file_path']}"
    try:
        response = requests.get(url, timeout=60)
    except requests.RequestException:
        raise ValueError("network error while downloading from Telegram") from None
    if response.status_code != 200:
        raise ValueError(f"Telegram answered HTTP {response.status_code} for the file")
    return response.content
