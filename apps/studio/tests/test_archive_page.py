"""
Control panel → Archive channel: the channels the bot heard from (a post
there, or being made admin) with their IDs, and picking one, which the bot
proves by posting a line there.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from apps.core import runtime
from apps.core.models import TelegramUpdate, UpdateOutcome
from apps.core.services import telegram
from apps.core.tests.helpers import FakeTelegram, configured, post_update
from apps.studio import archive

CHANNEL = -1001234567890
PAGE = "/admin/core/controlpanel/archive/"


def channel_post(text="test", chat_id=CHANNEL, title="My archive", update_id=880001):
    return {"update_id": update_id, "channel_post": {
        "message_id": 5, "date": 1, "text": text,
        "chat": {"id": chat_id, "type": "channel", "title": title},
        "sender_chat": {"id": chat_id, "type": "channel", "title": title}}}


def a_channel(chat_id=CHANNEL, title="My archive"):
    return {"id": chat_id, "type": "channel", "title": title}


@configured
class ArchivePageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(User.objects.create_superuser("o", "o@example.com", "pw"))

    def test_telegram_is_asked_for_channel_posts(self):
        self.assertIn("channel_post", telegram.ALLOWED_UPDATES)

    def test_a_post_in_the_channel_shows_it_with_its_id(self):
        post_update(channel_post())
        row = TelegramUpdate.objects.get()
        self.assertEqual((row.kind, row.outcome, row.summary),
                         ("channel_post", UpdateOutcome.HANDLED, "test"))
        page = self.client.get(PAGE)
        self.assertContains(page, "My archive")
        self.assertContains(page, f"<code>{CHANNEL}</code>")
        self.assertContains(page, "Use as archive")
        self.assertIsNone(archive.chat_id(), "listed, not used, until it is picked")

    def test_being_made_admin_by_someone_else_lists_it_too(self):
        post_update({"update_id": 880002, "my_chat_member": {
            "chat": a_channel(), "from": {"id": 999},
            "old_chat_member": {"status": "left"},
            "new_chat_member": {"status": "administrator", "can_post_messages": True}}})
        self.assertEqual(archive.seen_channels()[0]["status"], "administrator")
        self.assertIsNone(archive.chat_id())

    def test_using_one(self):
        post_update(channel_post())
        with mock.patch.object(telegram, "get_chat", return_value=a_channel()):
            response = self.client.post(PAGE, {"action": "use", "chat": str(CHANNEL)},
                                        follow=True)
        self.assertContains(response, "“My archive” (-1001234567890) is now the archive channel.")
        self.assertEqual(archive.chat_id(), CHANNEL)
        sent = self.tg.last("sendMessage")
        self.assertEqual((sent["chat_id"], sent["text"]),
                         (CHANNEL, "✅ This channel is now the AIstars archive."))

    def test_one_the_bot_may_not_post_in(self):
        self.tg.fail.add("sendMessage")
        with mock.patch.object(telegram, "get_chat", return_value=a_channel()):
            response = self.client.post(PAGE, {"action": "use", "chat": str(CHANNEL)},
                                        follow=True)
        self.assertContains(response, "The bot may not post in “My archive”")
        self.assertIsNone(archive.chat_id())

    def test_typing_a_username_or_an_unknown_one(self):
        with mock.patch.object(telegram, "get_chat", return_value=a_channel(title="Public")) \
                as get_chat:
            self.client.post(PAGE, {"action": "use", "chat": "@mychannel"})
        get_chat.assert_called_once_with("@mychannel")
        self.assertEqual(archive.chat_id(), CHANNEL)
        with mock.patch.object(telegram, "get_chat", return_value=None):
            response = self.client.post(PAGE, {"action": "use", "chat": "-1009"}, follow=True)
        self.assertContains(response, "Telegram does not know this channel")

    def test_stop_archiving(self):
        with mock.patch.object(telegram, "get_chat", return_value=a_channel()):
            self.client.post(PAGE, {"action": "use", "chat": str(CHANNEL)})
            response = self.client.post(PAGE, {"action": "stop"}, follow=True)
        self.assertContains(response, "No archive channel yet.")
        self.assertIsNone(archive.chat_id())

    def test_the_page_says_when_telegram_does_not_send_channel_posts(self):
        with mock.patch.object(telegram, "_call", side_effect=lambda method, **kw: {
                "url": "x", "allowed_updates": ["message"]} if method == "getWebhookInfo"
                else None):
            page = self.client.get(PAGE)
        self.assertContains(page, "Telegram does not send the bot the posts in channels yet")

    def test_the_control_panel_links_to_it(self):
        self.assertContains(self.client.get("/admin/core/controlpanel/"), 'href="archive/"')


class DoctorTests(TestCase):
    def test_it_warns_without_channel_posts(self):
        from apps.core import diagnostics

        cache.clear()
        with runtime.override(TELEGRAM_BOT_TOKEN="1:x"), mock.patch.object(
                telegram, "_call", side_effect=lambda method, **kw: {"username": "AIstarsBot"}
                if method == "getMe" else {"url": "", "allowed_updates": ["message"]}):
            checks = {check.label: check for check in diagnostics.run().checks}
        self.assertEqual(checks["Channel posts"].status, diagnostics.WARN)
