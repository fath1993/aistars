import hmac
import json
import logging

from django.core.cache import cache
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.billing import bot as billing_bot
from apps.studio import archive
from apps.studio import bot as studio_bot

from . import activity, languages, menus, registration, runtime, updates
from .models import Account, LogCategory
from .services import telegram, ui

logger = logging.getLogger(__name__)

# Telegram retries any non-2xx. Every handled path returns 200 -- a refusal is
# a chat message, never an HTTP status.
OK = HttpResponse(status=200)

# Timestamp of the most recent accepted update; diagnostics compares it with
# Telegram's last delivery error.
LAST_DELIVERY_KEY = "core:last_delivery"


@csrf_exempt
@require_POST
def telegram_webhook(request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = runtime.get("TELEGRAM_WEBHOOK_SECRET")
    if not expected or not hmac.compare_digest(secret, str(expected)):
        logger.warning("webhook rejected: bad secret token")
        updates.rejected(request.body)
        # Recorded, so "Telegram delivers and we reject" is distinguishable
        # from "Telegram does not deliver". Throttled: a misconfigured webhook
        # retries hard.
        _record_throttled(
            "webhook_rejected",
            "an update arrived but the secret token did not match"
            if secret else "an update arrived with no secret token header",
            detail={"secret_configured": bool(expected),
                    "header_present": bool(secret)},
        )
        return HttpResponseForbidden()

    try:
        update = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("webhook rejected: malformed body")
        return OK

    _mark_delivery()
    if not isinstance(update, dict):
        return OK
    # Kept as it came (Telegram updates in the admin); one sent again because
    # the answer was slow is not handled twice.
    row = updates.receive(update)
    if row is None:
        return OK

    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
        elif "pre_checkout_query" in update:
            _handle_pre_checkout(update["pre_checkout_query"])
        elif "my_chat_member" in update:
            archive.on_member_update(update["my_chat_member"])
        # A channel_post needs nothing: kept above, it shows the channel on the
        # Archive channel page.
    except Exception as exc:
        # Never 500 back to Telegram; that buys an infinite retry loop.
        logger.exception("webhook handler failed for update %s", update.get("update_id"))
        activity.error(LogCategory.WEBHOOK, "handler_error", updates.error_text(exc),
                       detail={"update_id": update.get("update_id")})
        updates.failed(row, exc)
    else:
        updates.handled(row)
    return OK


def _mark_delivery():
    from django.core.cache import cache

    cache.set(LAST_DELIVERY_KEY, timezone.now().timestamp(), 60 * 60 * 24 * 7)


def _record_throttled(event, message, detail=None, seconds=60):
    """At most one entry per event per minute."""
    from django.core.cache import cache

    if cache.add(f"activity:throttle:{event}", 1, seconds):
        activity.warn(LogCategory.WEBHOOK, event, message, detail=detail)


def _account_for(from_user):
    account, created = Account.objects.select_related("country").get_or_create(
        telegram_user_id=from_user["id"],
        defaults={
            "username": from_user.get("username", "") or "",
            "display_name": (from_user.get("first_name", "") or "").strip(),
            "language_code": from_user.get("language_code", "") or "",
        },
    )
    if created:
        activity.info(LogCategory.ACCOUNT, "first_contact",
                      f"{account} messaged the bot for the first time",
                      account=account)
    # The Telegram language follows the app: it is a fallback for the bot's.
    account.language_code = from_user.get("language_code", "") or account.language_code
    Account.objects.filter(pk=account.pk).update(
        last_seen_at=timezone.now(), language_code=account.language_code)
    return account


def _gate(account):
    """
    Why this account may not use the bot yet, or '' if it may.

    'blocked': blocked in the admin. 'register': no phone number yet.
    'region': the number's country is not allowed (or it has none).
    """
    if account.is_blocked:
        return "blocked"
    if not account.is_registered:
        return "register"
    if not account.region_allowed:
        return "region"
    return ""


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

def _handle_message(message):
    account = _account_for(message["from"])
    with languages.activate_for(account):
        _route_message(account, message)


# Files: photos, documents, voice notes and audio go to the AI tools; a video
# is answered there too ("not read yet"). Stickers are ignored.
MEDIA_KEYS = ("photo", "document", "voice", "audio", "video", "animation", "video_note")


def _route_message(account, message):
    chat_id = message["chat"]["id"]
    # Paid is paid: credited whatever else is true of the account by now.
    if message.get("successful_payment"):
        billing_bot.handle_successful_payment(account, message)
        return
    gate = _gate(account)

    if gate == "register" and message.get("contact"):
        _register(account, message)
        return
    if not gate and any(message.get(key) for key in MEDIA_KEYS):
        billing_bot.cancel_input(account)
        if not studio_bot.handle_media(account, chat_id, message):
            ui.fresh_screen(account, chat_id, menus.home_text(account),
                            menus.home_keyboard(account))
        return
    text = (message.get("text") or "").strip()
    if not text:
        return
    if gate == "blocked":
        telegram.send_message(chat_id, menus.blocked_text())
        return
    if gate == "register":
        if _command(text) == "start" and _payload(text):
            # A shared link: opened once the user has registered.
            cache.set(_start_key(account), _payload(text), 60 * 60 * 24)
        telegram.send_message(chat_id, menus.register_text(),
                              reply_markup=menus.register_keyboard())
        return
    if gate == "region":
        telegram.send_message(chat_id, menus.region_text())
        return

    command = _command(text)
    if command:
        # A command ends any question asked.
        billing_bot.cancel_input(account)
        studio_bot.stop_waiting(account)
    if command == "start" and _payload(text) and studio_bot.open_start(
            account, chat_id, _payload(text)):
        return
    if command in ("start", "menu"):
        ui.fresh_screen(account, chat_id, menus.home_text(account),
                        menus.home_keyboard(account))
        return
    if command == "help":
        ui.fresh_screen(account, chat_id, menus.help_text(), menus.back_keyboard())
        return
    if command == "privacy":
        ui.fresh_screen(account, chat_id, studio_bot.privacy_text(), menus.back_keyboard())
        return
    if command and billing_bot.handle_command(account, chat_id, command):
        return
    if command and studio_bot.handle_command(account, chat_id, command):
        return
    if command:
        telegram.send_message(chat_id, menus.unknown_command_text(),
                              reply_markup=menus.notice_keyboard())
        return
    if billing_bot.handle_text(account, chat_id, text):
        return
    # Anything else typed is for the AI: an answer to a question the tools
    # asked, or a chat message.
    if studio_bot.handle_text(account, chat_id, text):
        return

    # The tools are not open to this account yet: bring the panel back.
    ui.fresh_screen(account, chat_id, menus.home_text(account), menus.home_keyboard(account))


def _handle_pre_checkout(query):
    account = _account_for(query["from"])
    with languages.activate_for(account):
        billing_bot.handle_pre_checkout(account, query)


def _register(account, message):
    """The contact button was pressed: accept the user's own number only."""
    chat_id = message["chat"]["id"]
    contact = message["contact"]
    if contact.get("user_id") != message["from"]["id"]:
        telegram.send_message(chat_id, menus.not_own_number_text(),
                              reply_markup=menus.register_keyboard())
        return

    registration.register(account, contact.get("phone_number", ""))
    country = account.country.code if account.country else "none"
    if not account.region_allowed:
        activity.warn(LogCategory.ACCOUNT, "region_refused",
                      f"{account} registered from {country}, not an allowed country",
                      account=account, detail={"country": country})
        telegram.send_message(chat_id, menus.region_text(),
                              reply_markup=menus.remove_keyboard())
        return

    activity.info(LogCategory.ACCOUNT, "registered",
                  f"{account} registered from {country}", account=account,
                  detail={"country": country})
    # The country is known now, and with it maybe a different language.
    with languages.activate_for(account):
        telegram.send_message(chat_id, menus.registered_text(),
                              reply_markup=menus.remove_keyboard())
        payload = cache.get(_start_key(account))
        cache.delete(_start_key(account))
        if payload and studio_bot.open_start(account, chat_id, payload):
            return
        ui.fresh_screen(account, chat_id, menus.home_text(account),
                        menus.home_keyboard(account))


def _command(text: str) -> str:
    """'/Start@MyBot arg' -> 'start'; '' for anything that is not a command."""
    if not text.startswith("/"):
        return ""
    return text.split()[0].lower().lstrip("/").split("@")[0]


def _payload(text: str) -> str:
    """'/start agent_lawyer' -> 'agent_lawyer' (what a t.me/<bot>?start= link carries)."""
    parts = text.split(maxsplit=1)
    return parts[1].strip()[:64] if len(parts) > 1 else ""


def _start_key(account):
    return f"start:payload:{account.pk}"


# --------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------

def _handle_callback(callback_query):
    account = _account_for(callback_query["from"])
    with languages.activate_for(account):
        _route_callback(account, callback_query)


def _route_callback(account, callback_query):
    callback_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    gate = _gate(account)
    if gate == "blocked":
        telegram.answer_callback_query(callback_id, menus.blocked_text())
        return
    if gate == "register":
        telegram.answer_callback_query(callback_id, menus.register_first_text())
        telegram.send_message(chat_id, menus.register_text(),
                              reply_markup=menus.register_keyboard())
        return
    if gate == "region":
        telegram.answer_callback_query(callback_id, menus.region_text())
        return

    verb, _, argument = data.partition(":")
    # The message tapped becomes the panel, edited in place; except buttons on
    # messages that are not the panel (an AI reply, an image, a notice), which
    # leave the message as it is.
    if verb != "out" and data != "menu:new":
        ui.adopt(account, message_id)
    # Another button ends a question asked: typed text is no longer its answer.
    if verb != "credits":
        billing_bot.cancel_input(account)

    if verb == "menu":
        studio_bot.stop_waiting(account)
        _menu(account, chat_id, argument)
        telegram.answer_callback_query(callback_id)
        return
    if verb == "lang":
        studio_bot.stop_waiting(account)
        _language(account, chat_id, argument)
        telegram.answer_callback_query(callback_id)
        return
    if verb == "credits":
        studio_bot.stop_waiting(account)
        billing_bot.handle_callback(account, chat_id, argument)
        telegram.answer_callback_query(callback_id)
        return
    answer = studio_bot.handle_callback(account, chat_id, verb, argument, message_id)
    if answer is not None:
        telegram.answer_callback_query(callback_id, answer)
        return

    # A button from an older version of the bot.
    telegram.answer_callback_query(callback_id, menus.expired_button_text())
    ui.screen(account, chat_id, menus.home_text(account), menus.home_keyboard(account))


def _menu(account, chat_id, section):
    if section == "help":
        ui.screen(account, chat_id, menus.help_text(), menus.back_keyboard())
    elif section == "privacy":
        ui.screen(account, chat_id, studio_bot.privacy_text(),
                  menus.back_keyboard("menu:profile"))
    elif section == "profile":
        ui.screen(account, chat_id, menus.profile_text(account),
                  menus.profile_keyboard(account))
    elif section == "new":
        ui.fresh_screen(account, chat_id, menus.home_text(account),
                        menus.home_keyboard(account))
    else:
        ui.screen(account, chat_id, menus.home_text(account), menus.home_keyboard(account))


def _language(account, chat_id, code):
    """`lang:` shows the choice; `lang:<code>` sets it and shows the profile."""
    used = languages.used_codes()
    if code not in used:
        ui.screen(account, chat_id, menus.language_text(),
                  menus.language_keyboard(languages.resolve(account), used))
        return

    Account.objects.filter(pk=account.pk).update(language=code)
    account.language = code
    activity.info(LogCategory.ACCOUNT, "language_changed",
                  f"{account} chose {code}", account=account)
    with languages.activate_for(account):
        ui.screen(account, chat_id, menus.profile_text(account),
                  menus.profile_keyboard(account))
