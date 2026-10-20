"""Shared test helpers. Not a test module: nothing here is collected."""

import itertools
import json
from unittest import mock

from django.test import Client

from apps.core import runtime

SECRET = "test-secret"
TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


class FakeTelegram:
    """
    Stands in for telegram._call and records every Bot API call.

    sendMessage answers with a fresh message_id so the panel logic has
    something to remember; set `fail` to a method name to make it return None.
    """

    def __init__(self):
        self.calls = []
        self.fail = set()
        self._ids = itertools.count(1000)

    def __call__(self, method, **payload):
        self.calls.append((method, payload))
        if method in self.fail or (method, payload.get("chat_id")) in self.fail:
            return None
        if method == "sendMessage":
            return {"message_id": next(self._ids)}
        if method == "editMessageText":
            return {"message_id": payload.get("message_id")}
        if method == "getMe":
            return {"username": "AIstarsBot"}
        if method in ("sendVideo", "sendPhoto", "sendDocument", "sendVoice", "sendAudio"):
            # A new upload gets a new file_id; a file_id is sent as itself.
            kind = method[4:].lower()
            file_id = payload.get(kind) if isinstance(payload.get(kind), str) \
                else f"{kind}-{next(self._ids)}"
            sent = [{"file_id": file_id}] if kind == "photo" else {"file_id": file_id}
            return {"message_id": next(self._ids), kind: sent}
        if method == "getFile":
            return {"file_id": payload.get("file_id"), "file_path": "photos/file_1.jpg",
                    "file_size": 1000}
        return True

    def methods(self):
        return [method for method, _ in self.calls]

    def last(self, method):
        for name, payload in reversed(self.calls):
            if name == method:
                return payload
        return None

    def patch(self):
        return mock.patch("apps.core.services.telegram._call", self)


def keyboard_callbacks(payload) -> list:
    """All callback_data values in a sent reply_markup."""
    markup = json.loads(payload["reply_markup"])
    return [button["callback_data"]
            for row in markup["inline_keyboard"] for button in row]


def post_update(update, secret=SECRET):
    headers = {"HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": secret} if secret else {}
    return Client().post("/bot/webhook/", data=json.dumps(update),
                         content_type="application/json", **headers)


_update_ids = itertools.count(1)


def message_update(text, user_id=42, first_name="Amir"):
    return {
        "update_id": next(_update_ids),
        "message": {
            "message_id": next(_update_ids),
            "from": {"id": user_id, "first_name": first_name,
                     "username": "amir", "language_code": "en"},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        },
    }


def open_panel(user_id=42):
    """/start: the home panel shows. Returns its message_id, which the panel's
    buttons are tapped on."""
    from apps.core.models import Account

    post_update(message_update("/start", user_id=user_id))
    return Account.objects.get(telegram_user_id=user_id).ui_message_id


def callback_update(data, message_id, user_id=42):
    return {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": f"cb{next(_update_ids)}",
            "from": {"id": user_id, "first_name": "Amir"},
            "message": {"message_id": message_id,
                        "chat": {"id": user_id, "type": "private"}},
            "data": data,
        },
    }


configured = runtime.override(TELEGRAM_WEBHOOK_SECRET=SECRET,
                              TELEGRAM_BOT_TOKEN=TOKEN)


def register(user_id=42, country="GB", language="", **fields):
    """An account that has shared its number from `country` (allowed by the seed)."""
    from django.utils import timezone

    from apps.core.models import Account, Country

    account, _ = Account.objects.update_or_create(
        telegram_user_id=user_id,
        defaults={"registered_at": timezone.now(), "phone": "+447700900123",
                  "country": Country.objects.get(code=country), "language": language,
                  "language_code": "en", **fields},
    )
    return account


def contact_update(phone, user_id=42, contact_user_id=None, language_code="en"):
    """The message Telegram sends when the contact button is pressed."""
    return {
        "update_id": next(_update_ids),
        "message": {
            "message_id": next(_update_ids),
            "from": {"id": user_id, "first_name": "Amir", "language_code": language_code},
            "chat": {"id": user_id, "type": "private"},
            "contact": {"phone_number": phone, "first_name": "Amir",
                        "user_id": user_id if contact_user_id is None else contact_user_id},
        },
    }


def complete_translations(*codes):
    """Every active text translated in `codes`, as after a successful AI run."""
    from apps.core.models import Language, SourceText, Translation, TranslationStatus

    for code in codes:
        for source in SourceText.objects.filter(active=True).exclude(
                translations__language_id=code):
            Translation.objects.create(language_id=code, source=source,
                                       text=f"[{code}] {source.text}", model="test")
        Language.objects.filter(code=code).update(status=TranslationStatus.DONE,
                                                  status_detail="")


def seed_translations():
    """A few texts in Romanian and Russian, as an AI run would have left them."""
    from apps.core.models import SourceText, Translation

    for code, texts in (("ro", {"Help": "Ajutor", "Profile": "Profil"}),
                        ("ru", {"Help": "Помощь", "Profile": "Профиль"})):
        for english, text in texts.items():
            Translation.objects.update_or_create(
                language_id=code, source=SourceText.objects.get(text=english),
                defaults={"text": text, "model": "test"})
