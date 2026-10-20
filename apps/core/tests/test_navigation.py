"""
Navigation is inline buttons only. Every screen but home ends with ‹ Back ·
🏠 Menu. A message that is not the panel (a notice, an answer) has 🏠 Menu,
which keeps it and sends the panel under it. Nothing is ever put under the
typing field, and there is no ☰ Menu command list.
"""
import json

from django.core.cache import cache
from django.test import TestCase

from apps.billing.models import CreditRequest
from apps.core import diagnostics, runtime
from apps.core.models import Account

from .helpers import (
    SECRET,
    TOKEN,
    FakeTelegram,
    callback_update,
    configured,
    contact_update,
    keyboard_callbacks,
    message_update,
    post_update,
    register,
)

BASE_URL = "https://ais.example.com"


def last_row(payload):
    rows = json.loads(payload["reply_markup"])["inline_keyboard"]
    return [button.get("callback_data") for button in rows[-1]]


class NavigationTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        register(42, tester=True)
        post_update(message_update("/start"))
        self.home = self.panel()

    def sent(self, method="sendMessage"):
        return [payload for name, payload in self.tg.calls if name == method]

    def panel(self):
        return Account.objects.get(telegram_user_id=42).ui_message_id

    def tap(self, data, message_id=None):
        post_update(callback_update(data, message_id or self.panel()))
        return self.tg.last("editMessageText")


@configured
class ScreenTests(NavigationTestCase):
    def test_home_has_neither(self):
        callbacks = keyboard_callbacks(self.sent()[-1])
        self.assertNotIn("menu:home", callbacks)
        self.assertEqual(callbacks, ["chat:new", "ag:", "img:", "voice:", "credits:", "use:",
                                     "menu:profile", "menu:help"])

    def test_every_other_screen_ends_with_back_and_menu(self):
        back_to = {
            "menu:help": "menu:home",
            "menu:profile": "menu:home",
            "lang:": "menu:profile",
            "credits:": "menu:home",
            "credits:buy": "credits:",
            "credits:statement:0": "credits:",
            "credits:request": "credits:",
            "chat:": "menu:home",
            "chat:models": "chat:",
            "chat:list:0": "chat:",
            "img:": "menu:home",
            "img:create": "menu:home",
            "voice:": "menu:home",
            "voice:voices": "voice:",
            "chat:new": "menu:home",
            "ag:": "menu:home",
            "ag:cat:law": "ag:",
            "ag:card:lawyer": "ag:back",
            "ag:search": "ag:",
            "ag:find": "ag:",
            "ag:saved": "ag:",
            "ag:top": "ag:",
            "use:": "menu:home",
            "use:last:0": "use:",
            "use:limit": "use:",
            "me:": "menu:profile",
            "menu:privacy": "menu:profile",
            "chat:search": "chat:list:0",
        }
        for data, target in back_to.items():
            edit = self.tap(data)
            self.assertEqual(edit["message_id"], self.home, data)
            if data == "img:create":   # a question: Cancel, then the menu
                self.assertEqual(keyboard_callbacks(edit), ["img:", "menu:home"])
                continue
            self.assertEqual(last_row(edit), [target, "menu:home"], data)

    def test_menu_ends_a_credit_request(self):
        self.tap("credits:request")
        self.tap("menu:home")
        self.assertIn("Just type to chat", self.tg.last("editMessageText")["text"])
        post_update(message_update("500"))
        self.assertFalse(CreditRequest.objects.exists())
        # Typed text is a chat message now: here, too few credits to answer it.
        self.assertIn("Not enough credits", self.sent()[-1]["text"])


@configured
class NoticeTests(NavigationTestCase):
    def test_command_answers_have_the_menu(self):
        for command in ("/terms", "/support", "/paysupport", "/nope"):
            post_update(message_update(command))
            self.assertEqual(keyboard_callbacks(self.sent()[-1]), ["menu:new"], command)

    def test_menu_keeps_the_notice_and_sends_the_panel_under_it(self):
        notice = 777
        self.tg.calls.clear()
        self.tap("menu:new", notice)
        touched = [payload["message_id"] for name, payload in self.tg.calls
                   if name in ("editMessageText", "deleteMessage")]
        self.assertNotIn(notice, touched)
        self.assertEqual(touched, [self.home])   # the old panel goes
        self.assertIn("Just type to chat", self.sent()[-1]["text"])
        self.assertNotIn(self.panel(), (notice, self.home))


@configured
class KeyboardTests(NavigationTestCase):
    def test_nothing_puts_a_menu_under_the_typing_field(self):
        post_update(contact_update("+447700900456", user_id=77))
        post_update(message_update("/start"))
        self.tap("menu:profile")
        for text in ("/terms", "/credits", "hello"):
            post_update(message_update(text))
        for payload in self.sent():
            markup = json.loads(payload.get("reply_markup") or "{}")
            self.assertNotIn("keyboard", markup)


@runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                  PUBLIC_BASE_URL=BASE_URL)
class CommandTests(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_registering_the_webhook_sets_no_command_list(self):
        ok, note = diagnostics.register_webhook()
        self.assertTrue(ok)
        self.assertEqual(note, f"Webhook registered: {BASE_URL}/bot/webhook/.")
        self.assertNotIn("setMyCommands", self.tg.methods())
        self.assertNotIn("deleteMyCommands", self.tg.methods())
