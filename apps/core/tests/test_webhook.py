from django.test import TestCase

from apps.core import menus
from apps.core.models import Account, AccountStatus, ActivityLog, LogCategory

from .helpers import (
    FakeTelegram,
    callback_update,
    configured,
    keyboard_callbacks,
    message_update,
    post_update,
    register,
)


@configured
class WebhookSecretTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        # The rejection log is throttled through the cache.
        cache.clear()

    def test_missing_secret_is_rejected(self):
        response = post_update(message_update("/start"), secret="")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Account.objects.exists())

    def test_wrong_secret_is_rejected_and_logged(self):
        response = post_update(message_update("/start"), secret="wrong")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ActivityLog.objects.filter(event="webhook_rejected").exists())

    def test_get_is_not_allowed(self):
        from django.test import Client

        self.assertEqual(Client().get("/bot/webhook/").status_code, 405)

    def test_malformed_body_still_answers_200(self):
        from django.test import Client

        response = Client().post(
            "/bot/webhook/", data="{not json", content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="test-secret")
        self.assertEqual(response.status_code, 200)

    def test_a_crashing_handler_answers_200_and_is_logged(self):
        from unittest import mock

        with mock.patch("apps.core.views._handle_message", side_effect=RuntimeError("boom")):
            response = post_update(message_update("/start"))
        self.assertEqual(response.status_code, 200)
        log = ActivityLog.objects.get(event="handler_error")
        self.assertEqual(log.category, LogCategory.WEBHOOK)


@configured
class StartTests(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        register(42)

    def test_start_creates_the_account(self):
        post_update(message_update("/start", user_id=777, first_name="Sara"))
        account = Account.objects.get(telegram_user_id=777)
        self.assertEqual(account.display_name, "Sara")
        self.assertEqual(account.status, AccountStatus.ACTIVE)
        self.assertIsNotNone(account.last_seen_at)
        self.assertTrue(ActivityLog.objects.filter(event="first_contact").exists())

    def test_start_sends_the_home_panel_and_remembers_it(self):
        post_update(message_update("/start"))
        self.assertEqual(self.tg.methods(), ["sendMessage"])
        sent = self.tg.last("sendMessage")
        self.assertEqual(sent["text"], menus.home_text())
        self.assertEqual(keyboard_callbacks(sent), ["credits:", "menu:profile", "menu:help"])
        account = Account.objects.get(telegram_user_id=42)
        self.assertIsNotNone(account.ui_message_id)

    def test_second_start_replaces_the_old_panel(self):
        post_update(message_update("/start"))
        first_panel = Account.objects.get(telegram_user_id=42).ui_message_id

        post_update(message_update("/start"))
        self.assertEqual(self.tg.last("deleteMessage")["message_id"], first_panel)
        second_panel = Account.objects.get(telegram_user_id=42).ui_message_id
        self.assertNotEqual(second_panel, first_panel)

    def test_start_with_bot_mention_and_payload_is_still_start(self):
        post_update(message_update("/start@AIstarsBot ref123"))
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.home_text())

    def test_menu_command_shows_the_home_panel(self):
        post_update(message_update("/menu"))
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.home_text())

    def test_help_command_shows_help_with_back_and_menu(self):
        post_update(message_update("/help"))
        sent = self.tg.last("sendMessage")
        self.assertEqual(sent["text"], menus.help_text())
        self.assertEqual(keyboard_callbacks(sent), ["menu:home", "menu:home"])

    def test_unknown_command_is_answered(self):
        post_update(message_update("/nope"))
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.unknown_command_text())

    def test_plain_text_brings_the_panel_back(self):
        post_update(message_update("hello"))
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.home_text())

    def test_a_message_without_text_is_ignored(self):
        update = message_update("x")
        del update["message"]["text"]
        post_update(update)
        self.assertEqual(self.tg.calls, [])

    def test_blocked_account_gets_one_line_and_no_panel(self):
        Account.objects.filter(telegram_user_id=42).update(status=AccountStatus.BLOCKED)
        post_update(message_update("/start"))
        self.assertEqual(self.tg.methods(), ["sendMessage"])
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.blocked_text())


@configured
class ButtonTests(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        register(42)
        post_update(message_update("/start"))
        self.panel = Account.objects.get(telegram_user_id=42).ui_message_id
        self.tg.calls.clear()

    def test_help_button_edits_the_panel_in_place(self):
        post_update(callback_update("menu:help", self.panel))
        edit = self.tg.last("editMessageText")
        self.assertEqual(edit["message_id"], self.panel)
        self.assertEqual(edit["text"], menus.help_text())
        self.assertNotIn("sendMessage", self.tg.methods())
        self.assertIn("answerCallbackQuery", self.tg.methods())

    def test_profile_button_shows_the_account(self):
        post_update(callback_update("menu:profile", self.panel))
        text = self.tg.last("editMessageText")["text"]
        self.assertIn("Profile", text)
        self.assertIn("<code>42</code>", text)

    def test_back_returns_home(self):
        post_update(callback_update("menu:home", self.panel))
        self.assertEqual(self.tg.last("editMessageText")["text"], menus.home_text())

    def test_tapping_an_older_panel_makes_it_the_panel(self):
        post_update(callback_update("menu:help", 5))
        self.assertEqual(self.tg.last("editMessageText")["message_id"], 5)
        self.assertEqual(Account.objects.get(telegram_user_id=42).ui_message_id, 5)

    def test_a_failed_edit_falls_back_to_a_new_panel(self):
        self.tg.fail.add("editMessageText")
        post_update(callback_update("menu:help", self.panel))
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.help_text())

    def test_an_unknown_button_is_explained(self):
        post_update(callback_update("old:thing", self.panel))
        answer = self.tg.last("answerCallbackQuery")
        self.assertEqual(answer["text"], "That button has expired.")
        self.assertEqual(self.tg.last("editMessageText")["text"], menus.home_text())

    def test_blocked_account_cannot_use_buttons(self):
        Account.objects.filter(telegram_user_id=42).update(status=AccountStatus.BLOCKED)
        post_update(callback_update("menu:help", self.panel))
        self.assertEqual(self.tg.methods(), ["answerCallbackQuery"])
        self.assertEqual(self.tg.last("answerCallbackQuery")["text"], menus.blocked_text())

    def test_profile_escapes_the_users_name(self):
        Account.objects.filter(telegram_user_id=42).update(display_name="<b>x&y")
        post_update(callback_update("menu:profile", self.panel))
        self.assertIn("&lt;b&gt;x&amp;y", self.tg.last("editMessageText")["text"])
