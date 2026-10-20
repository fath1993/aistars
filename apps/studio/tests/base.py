"""
Shared set-up for the AI tools' tests: a registered tester with credits, the
bot reached through its webhook, and OpenAI stood in for by FakeOpenAI. Not a
test module: nothing here is collected.
"""

import base64
import itertools
import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from apps.ai.models import Provider
from apps.billing import ledger
from apps.billing.models import EntryKind
from apps.core.models import Account, ControlPanel
from apps.core.tests.helpers import (
    FakeTelegram,
    callback_update,
    configured,
    keyboard_callbacks,
    message_update,
    post_update,
    register,
)

_ids = itertools.count(5000)


class Response:
    """A requests response: JSON, bytes, or a server-sent event stream."""

    def __init__(self, body=None, content=b"", status=200, events=None):
        self.body, self.content, self.status_code, self.events = body, content, status, events
        self.encoding = None

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self, decode_unicode=False):
        for event in self.events or []:
            yield "data: " + json.dumps(event)
        yield "data: [DONE]"


class FakeOpenAI:
    """
    Answers every OpenAI call the bot makes, and records them. Set `answer`
    (the chat's words), `usage`, `flag` (Moderation categories), `judge`,
    `image_usage`, `transcript`, or `fail` (a path that answers an error).
    """

    def __init__(self):
        self.calls = []
        self.answer = "Hello! **Here** you are."
        self.usage = {"prompt_tokens": 1000, "completion_tokens": 200}
        self.finish = "stop"
        self.flag = {}
        self.judge = '{"verdict": "allow", "category": "none", "reason": "fine"}'
        self.image_usage = {"input_tokens": 1100, "output_tokens": 1056,
                            "input_tokens_details": {"text_tokens": 100, "image_tokens": 1000}}
        self.transcript = "What is the weather like on Mars?"
        self.fail = {}

    def paths(self):
        return [url.split("/v1/")[-1] for url, _ in self.calls]

    def sent(self, path):
        return [kwargs for url, kwargs in self.calls if url.endswith(path)]

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        path = url.split("/v1/")[-1]
        if path in self.fail:
            status, message = self.fail[path]
            return Response({"error": {"message": message,
                                       "code": "moderation_blocked" if status == 451 else ""}},
                            status=400 if status == 451 else status)
        if path == "moderations":
            return Response({"results": [{"flagged": bool(self.flag), "categories": self.flag}]})
        if path == "chat/completions":
            if kwargs.get("stream"):
                words = [self.answer[i:i + 7] for i in range(0, len(self.answer), 7)]
                events = [{"choices": [{"delta": {"content": piece}, "finish_reason": None}]}
                          for piece in words]
                events.append({"choices": [{"delta": {}, "finish_reason": self.finish}]})
                events.append({"choices": [], "usage": self.usage})
                return Response(events=events)
            # The safety judge (and translation): one answer.
            return Response({"choices": [{"message": {"content": self.judge}}],
                             "usage": {"prompt_tokens": 800, "completion_tokens": 30}})
        if path in ("images/generations", "images/edits"):
            return Response({"data": [{"b64_json": base64.b64encode(b"PNG-image").decode()}],
                             "usage": self.image_usage})
        if path == "audio/transcriptions":
            return Response({"text": self.transcript})
        if path == "audio/speech":
            return Response(content=b"OggS-speech")
        raise AssertionError(f"unexpected call to {url}")


@configured
class StudioTestCase(TestCase):
    """A tester with 🪙 500, OpenAI with a key, Demo mode off: the live path."""

    demo = False
    credits = 500

    def setUp(self):
        cache.clear()
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.openai = FakeOpenAI()
        for target in ("apps.ai.clients.requests.post", "apps.safety.checks.requests.post"):
            patcher = mock.patch(target, side_effect=self.openai)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("apps.core.services.telegram.download_file",
                             side_effect=self.download)
        patcher.start()
        self.addCleanup(patcher.stop)
        Provider.objects.filter(kind="openai").update(api_key="sk-test")
        ControlPanel.objects.update_or_create(singleton=1, defaults={"demo_mode": self.demo})
        self.account = register(42, tester=True)
        if self.credits:
            ledger.post(self.account, EntryKind.PURCHASE, self.credits)
        post_update(message_update("/start"))

    def download(self, file_id):
        from apps.safety.tests.test_checks import jpeg

        return b"OggS-voice" if file_id.startswith("voice") else jpeg()

    # ---- the user ----------------------------------------------------------

    def say(self, text):
        post_update(message_update(text))

    def send_photo(self, file_id="photo-1", caption=""):
        update = message_update("")
        message = update["message"]
        message.pop("text")
        message["photo"] = [{"file_id": f"{file_id}-small"}, {"file_id": file_id}]
        if caption:
            message["caption"] = caption
        post_update(update)

    def send_voice(self, file_id="voice-1", seconds=12, kind="voice"):
        update = message_update("")
        message = update["message"]
        message.pop("text")
        message[kind] = {"file_id": file_id, "duration": seconds}
        post_update(update)

    def tap(self, data, message_id=None):
        """A button on the panel (or on `message_id`); the toast it answered with."""
        target = message_id or Account.objects.get(pk=self.account.pk).ui_message_id
        post_update(callback_update(data, target))
        return self.tg.last("answerCallbackQuery").get("text", "")

    # ---- what the bot did ----------------------------------------------------

    def messages(self):
        """Every text the bot sent or wrote into a message, in order."""
        return [payload["text"] for method, payload in self.tg.calls
                if method in ("sendMessage", "editMessageText") and payload.get("chat_id") == 42]

    def last_text(self):
        return self.messages()[-1]

    def last_payload(self, *methods):
        for method, payload in reversed(self.tg.calls):
            if method in (methods or ("sendMessage", "editMessageText")) \
                    and payload.get("chat_id") == 42:
                return payload
        return None

    def buttons(self, payload=None):
        payload = payload or self.last_payload()
        return keyboard_callbacks(payload) if payload and payload.get("reply_markup") else []

    def balance(self):
        return ledger.balance(self.account)
