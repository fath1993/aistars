import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from apps.core import runtime
from apps.core.models import Account, ActivityLog
from apps.core.services import telegram, ui

from .helpers import TOKEN, FakeTelegram


def _response(body):
    response = mock.Mock()
    response.json.return_value = body
    return response


@runtime.override(TELEGRAM_BOT_TOKEN=TOKEN)
class CallTests(TestCase):
    def test_a_successful_call_returns_the_result(self):
        with mock.patch("apps.core.services.telegram.requests.post",
                        return_value=_response({"ok": True, "result": {"id": 1}})) as post:
            self.assertEqual(telegram._call("getMe"), {"id": 1})
        self.assertTrue(post.call_args.args[0].endswith(f"/bot{TOKEN}/getMe"))

    def test_an_api_error_returns_none(self):
        with mock.patch("apps.core.services.telegram.requests.post",
                        return_value=_response({"ok": False, "description": "Bad Request"})):
            self.assertIsNone(telegram._call("sendMessage", chat_id=1, text="x"))

    def test_a_network_error_returns_none(self):
        import requests

        with mock.patch("apps.core.services.telegram.requests.post",
                        side_effect=requests.ConnectionError):
            self.assertIsNone(telegram._call("getMe"))


class NoTokenTests(TestCase):
    def setUp(self):
        cache.clear()

    @runtime.override(TELEGRAM_BOT_TOKEN="")
    def test_no_token_skips_the_call_and_logs_once(self):
        with mock.patch("apps.core.services.telegram.requests.post") as post:
            self.assertIsNone(telegram._call("getMe"))
            self.assertIsNone(telegram._call("getMe"))
        post.assert_not_called()
        self.assertEqual(ActivityLog.objects.filter(event="no_bot_token").count(), 1)


class MessageTests(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_html_is_escaped(self):
        self.assertEqual(telegram.esc("<b>&"), "&lt;b&gt;&amp;")

    def test_link_previews_are_off(self):
        telegram.send_message(1, "hi")
        options = json.loads(self.tg.last("sendMessage")["link_preview_options"])
        self.assertTrue(options["is_disabled"])

    def test_an_overlong_message_is_cut_at_a_line(self):
        telegram.send_message(1, "line\n" * 2000)
        text = self.tg.last("sendMessage")["text"]
        self.assertLessEqual(len(text), telegram.MAX_MESSAGE)
        self.assertTrue(text.endswith("[truncated]"))

    def test_blocks_are_packed_and_only_the_last_gets_the_keyboard(self):
        blocks = ["x" * 3000, "y" * 3000, "z" * 10]
        telegram.send_blocks(1, blocks, reply_markup="KB")
        sends = [p for m, p in self.tg.calls if m == "sendMessage"]
        self.assertEqual(len(sends), 2)
        self.assertIsNone(sends[0]["reply_markup"])
        self.assertEqual(sends[1]["reply_markup"], "KB")

    def test_set_webhook_sends_the_secret_and_update_types(self):
        with runtime.override(TELEGRAM_WEBHOOK_SECRET="s3"):
            telegram.set_webhook("https://x.example.com/bot/webhook/")
        payload = self.tg.last("setWebhook")
        self.assertEqual(payload["secret_token"], "s3")
        self.assertIn("callback_query", payload["allowed_updates"])

    def test_bot_username_prefers_the_admin_value(self):
        cache.clear()
        with runtime.override(TELEGRAM_BOT_USERNAME="@Manual"):
            self.assertEqual(telegram.bot_username(), "Manual")
        self.assertNotIn("getMe", self.tg.methods())

    def test_bot_username_asks_telegram_once(self):
        cache.clear()
        self.assertEqual(telegram.bot_username(), "AIstarsBot")
        self.assertEqual(telegram.bot_username(), "AIstarsBot")
        self.assertEqual(self.tg.methods().count("getMe"), 1)


class PanelTests(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.account = Account.objects.create(telegram_user_id=1)

    def test_first_screen_is_sent_and_remembered(self):
        message_id = ui.screen(self.account, 1, "hi")
        self.assertEqual(self.tg.methods(), ["sendMessage"])
        self.account.refresh_from_db()
        self.assertEqual(self.account.ui_message_id, message_id)

    def test_later_screens_edit_the_same_message(self):
        first = ui.screen(self.account, 1, "one")
        second = ui.screen(self.account, 1, "two")
        self.assertEqual(first, second)
        self.assertEqual(self.tg.methods(), ["sendMessage", "editMessageText"])

    def test_fresh_screen_deletes_the_old_panel(self):
        old = ui.screen(self.account, 1, "one")
        new = ui.fresh_screen(self.account, 1, "two")
        self.assertNotEqual(old, new)
        self.assertEqual(self.tg.last("deleteMessage")["message_id"], old)
        self.account.refresh_from_db()
        self.assertEqual(self.account.ui_message_id, new)

    def test_a_failed_send_leaves_no_panel_recorded(self):
        self.tg.fail.add("sendMessage")
        self.assertIsNone(ui.screen(self.account, 1, "hi"))
        self.account.refresh_from_db()
        self.assertIsNone(self.account.ui_message_id)

    def test_notice_returns_an_id_that_clear_deletes(self):
        message_id = ui.notice(1, "working")
        ui.clear(1, message_id)
        self.assertEqual(self.tg.last("deleteMessage")["message_id"], message_id)
