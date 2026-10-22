"""
Telegram updates: every update is kept as it came, handled once however often
Telegram sends it, marked handled or failed (without the bot token), rejected
ones are recorded at most once a minute, the admin lists them, and they go
after TELEGRAM_UPDATES_KEEP_DAYS.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.core import runtime, updates
from apps.core.models import Account, TelegramUpdate, UpdateOutcome

from .helpers import (
    TOKEN,
    FakeTelegram,
    callback_update,
    configured,
    contact_update,
    message_update,
    post_update,
    register,
)


@configured
class RecordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_message_is_kept_as_it_came_and_marked_handled(self):
        update = message_update("/start")
        post_update(update)
        row = TelegramUpdate.objects.get()
        self.assertEqual((row.update_id, row.kind, row.user_id, row.summary, row.outcome),
                         (update["update_id"], "message", 42, "/start", UpdateOutcome.HANDLED))
        self.assertEqual(row.payload, update)
        self.assertIsNotNone(row.handled_at)

    def test_sent_again_it_is_handled_once(self):
        update = message_update("/start")
        post_update(update)
        sent = len(self.tg.calls)
        post_update(update)
        self.assertEqual(len(self.tg.calls), sent, "nothing done the second time")
        self.assertEqual(TelegramUpdate.objects.get().deliveries, 2)

    def test_a_failure_is_recorded_without_the_token(self):
        with mock.patch("apps.core.views._handle_message",
                        side_effect=RuntimeError(f"https://api.telegram.org/bot{TOKEN}/x")):
            response = post_update(message_update("hello"))
        self.assertEqual(response.status_code, 200)
        row = TelegramUpdate.objects.get()
        self.assertEqual(row.outcome, UpdateOutcome.FAILED)
        self.assertEqual(row.error, "RuntimeError: https://api.telegram.org/bot<token>/x")

    def test_a_wrong_secret_is_recorded_once_a_minute(self):
        update = message_update("/start")
        self.assertEqual(post_update(update, secret="wrong").status_code, 403)
        post_update(message_update("again"), secret="wrong")
        row = TelegramUpdate.objects.get()
        self.assertEqual((row.outcome, row.update_id, row.summary),
                         (UpdateOutcome.REJECTED, None, "/start"))
        # The real update with the same ID is still handled.
        post_update(update)
        self.assertEqual(TelegramUpdate.objects.get(update_id=update["update_id"]).outcome,
                         UpdateOutcome.HANDLED)

    def test_what_each_kind_says(self):
        register(42)
        post_update(callback_update("menu:help", 1))
        post_update(contact_update("+447700900456", user_id=77))
        post_update({"update_id": 990001, "my_chat_member": {
            "chat": {"id": -1001234567890, "type": "channel", "title": "Archive"},
            "from": {"id": 42}, "old_chat_member": {"status": "left"},
            "new_chat_member": {"status": "administrator"}}})
        photo = message_update("")
        photo["message"].pop("text")
        photo["message"]["document"] = {"file_id": "d", "file_name": "cv.pdf"}
        photo["message"]["caption"] = "Check it"
        post_update(photo)
        summaries = dict(TelegramUpdate.objects.values_list("kind", "summary")
                         .order_by("id"))
        self.assertEqual(summaries["callback_query"], "button menu:help")
        self.assertEqual(summaries["my_chat_member"],
                         "channel “Archive”: left → administrator")
        self.assertEqual(TelegramUpdate.objects.get(user_id=77).summary,
                         "shared a phone number")
        newest = TelegramUpdate.objects.filter(kind="message").order_by("id").last()
        self.assertEqual(newest.summary, "[file: cv.pdf] Check it")


class PurgeTests(TestCase):
    def test_old_updates_go(self):
        old = TelegramUpdate.objects.create(update_id=1, kind="message")
        TelegramUpdate.objects.filter(pk=old.pk).update(
            received_at=timezone.now() - timedelta(days=91))
        TelegramUpdate.objects.create(update_id=2, kind="message")
        from apps.core import tasks

        self.assertEqual(tasks.purge_telegram_updates(), 1)
        self.assertEqual(list(TelegramUpdate.objects.values_list("update_id", flat=True)), [2])

    @runtime.override(TELEGRAM_UPDATES_KEEP_DAYS=0)
    def test_kept_forever(self):
        TelegramUpdate.objects.create(update_id=1, kind="message")
        TelegramUpdate.objects.update(received_at=timezone.now() - timedelta(days=999))
        self.assertEqual(updates.purge(), 0)


@configured
class AdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(User.objects.create_superuser("o", "o@example.com", "pw"))

    def test_the_list_the_detail_and_the_link_from_an_account(self):
        account = register(42)
        post_update(message_update("Hello bot"))
        post_update(message_update("From a stranger", user_id=77))
        page = self.client.get("/admin/core/telegramupdate/")
        self.assertContains(page, "Hello bot")
        self.assertContains(page, f'/admin/core/account/{account.pk}/change/')
        page = self.client.get("/admin/core/telegramupdate/?user_id=42")
        self.assertContains(page, "Hello bot")
        self.assertNotContains(page, "From a stranger")
        row = TelegramUpdate.objects.get(user_id=42)
        page = self.client.get(f"/admin/core/telegramupdate/{row.pk}/change/")
        self.assertContains(page, "&quot;text&quot;: &quot;Hello bot&quot;")
        page = self.client.get(f"/admin/core/account/{account.pk}/change/")
        self.assertContains(page, "/admin/core/telegramupdate/?user_id=42")
        self.assertEqual(Account.objects.count(), 2)
