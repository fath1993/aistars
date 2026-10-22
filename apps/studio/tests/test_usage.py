"""
📊 Usage: what the user spent today and over 7 and 30 days, by tool, agent
and model; their last requests; the daily limit they set themselves; and 🧾
Details under images and voice results.
"""
from datetime import timedelta

from django.utils import timezone

from apps.ai.models import AIModel
from apps.studio import usage
from apps.studio.models import Job, Status

from .base import StudioTestCase


def luna():
    return AIModel.objects.get(model_id="gpt-6-luna")


class UsagePageTests(StudioTestCase):
    def test_nothing_yet(self):
        self.tap("use:")
        text = self.last_text()
        self.assertIn("📊 <b>Usage</b>", text)
        self.assertIn("Today: <b>💎 0</b> · Requests: 0", text)
        self.assertIn("Daily limit: none", text)
        self.assertIn("No requests yet.", text)
        self.assertEqual(self.buttons(), ["use:last:0", "use:limit", "credits:", "menu:home",
                                          "menu:home"])

    def test_what_was_spent(self):
        self.say("Hi")
        self.tap("ag:start:chef")
        self.say("Pasta?")
        Job.objects.create(account=self.account, kind="image", model=luna(), chat_id=42,
                           status=Status.DONE, charged=7)
        old = Job.objects.create(account=self.account, kind="chat", model=luna(), chat_id=42,
                                 status=Status.DONE, charged=4)
        Job.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=10))
        Job.objects.create(account=self.account, kind="chat", model=luna(), chat_id=42,
                           status=Status.FAILED, hold=9)
        self.say("/usage")
        text = self.last_text()
        self.assertIn("Today: <b>💎 9</b> · Requests: 3", text)
        self.assertIn("7 days: <b>💎 9</b> · Requests: 3", text)
        self.assertIn("30 days: <b>💎 13</b> · Requests: 4", text)
        self.assertIn("<b>Last 30 days by tool</b>\n🎨 Images: 💎 7\n💬 Chat: 💎 6", text)
        self.assertIn("<b>By agent</b>\n🤖 General assistant: 💎 5\n👨‍🍳 Chef: 💎 1", text)
        self.assertIn("<b>By model</b>\nGPT-6 Luna: 💎 13", text)

    def test_no_agent_used_shows_no_agent_lines(self):
        self.say("Hi")
        self.tap("use:")
        self.assertNotIn("By agent", self.last_text())

    def test_a_deleted_chat_still_counts_for_its_agent(self):
        self.tap("ag:start:chef")
        self.say("Pasta?")
        Job.objects.get().conversation.delete()
        self.assertEqual(usage.by_agent(self.account), [("👨‍🍳 Chef", 1)])

    def test_last_requests(self):
        self.say("Hi")
        Job.objects.create(account=self.account, kind="chat", model=luna(), chat_id=42,
                           status=Status.FAILED)
        self.tap("use:last:0")
        text = self.last_text()
        self.assertIn("🧾 <b>Last requests</b>", text)
        self.assertIn("💬 Chat answer · GPT-6 Luna · <b>nothing charged</b>", text)
        self.assertIn("💬 Chat answer · GPT-6 Luna · <b>💎 1</b>", text)
        self.assertEqual(self.buttons(), ["use:", "menu:home"])

    def test_last_requests_have_pages(self):
        for _ in range(usage.PER_PAGE + 1):
            Job.objects.create(account=self.account, kind="chat", model=luna(), chat_id=42,
                               status=Status.DONE, charged=1)
        self.tap("use:last:0")
        self.assertEqual(self.buttons()[0], "use:last:1")
        self.tap("use:last:1")
        self.assertEqual(self.buttons()[0], "use:last:0")

    def test_only_ones_own(self):
        from apps.core.tests.helpers import register

        Job.objects.create(account=register(77), kind="chat", model=luna(), chat_id=77,
                           status=Status.DONE, charged=50)
        self.tap("use:")
        self.assertIn("30 days: <b>💎 0</b>", self.last_text())


class LimitTests(StudioTestCase):
    def test_choosing_a_daily_limit(self):
        self.tap("use:limit")
        self.assertIn("The most your requests may cost in a day", self.last_text())
        self.assertEqual(self.buttons()[:3], ["use:setlimit:0", "use:setlimit:20",
                                              "use:setlimit:50"])
        self.assertEqual(self.tap("use:setlimit:50"), "🎯 💎 50")
        self.assertIn("Daily limit: 💎 50 · used today: 💎 0", self.last_text())
        from apps.studio.chat import profile

        self.assertEqual(profile(self.account).daily_limit, 50)
        self.tap("use:limit")
        self.assertIn("✓ 💎 50", self.button_texts())
        self.assertEqual(self.tap("use:setlimit:0"), "🎯 No limit")
        self.assertIsNone(profile(self.account).daily_limit)

    def test_only_the_amounts_offered(self):
        self.tap("use:setlimit:3")
        from apps.studio.chat import profile

        self.assertIsNone(profile(self.account).daily_limit)

    def test_an_image_past_the_limit(self):
        usage.set_limit(self.account, 1)
        self.tap("img:")
        self.tap("img:create")
        self.say("A red bicycle")
        self.tap("img:go")
        self.assertIn("Daily limit reached", self.last_text())
        self.assertFalse(Job.objects.exists())

    def test_a_text_read_aloud_past_the_limit(self):
        self.say("Hi")
        usage.set_limit(self.account, 1)
        answer = Job.objects.get()
        from apps.studio.models import Message

        self.tap(f"out:say:{Message.objects.get(role='assistant').pk}",
                 message_id=answer.message_id)
        self.assertIn("Daily limit reached", self.last_text())
        self.assertEqual(Job.objects.count(), 1)


class ResultDetailsTests(StudioTestCase):
    def test_an_image(self):
        self.tap("img:")
        self.tap("img:create")
        self.say("A red bicycle")
        self.tap("img:go")
        job = Job.objects.get()
        self.assertGreater(job.output_tokens, 0)
        details = self.tap(f"out:cost:{job.pk}", message_id=1)
        self.assertTrue(details.startswith(f"🎨 Image #{job.pk}\nGPT Image 2.5 Flare · "))
        self.assertIn("Tokens:", details)
        self.assertIn(f"charged 💎 {job.charged}", details)

    def test_a_voice_note_heard(self):
        self.tap("voice:stt")
        self.send_voice(seconds=84)
        job = Job.objects.get(kind="transcribe")
        markup = self.last_payload()
        self.assertIn(f"out:cost:{job.pk}", self.buttons(markup))
        details = self.tap(f"out:cost:{job.pk}", message_id=1)
        self.assertIn("GPT Transcribe · 1:24", details)
        self.assertNotIn("Tokens", details)

    def test_a_text_read_aloud(self):
        self.tap("voice:tts")
        self.say("Read this, please.")
        self.tap("voice:go")
        job = Job.objects.get(kind="speech")
        self.assertIn(f"out:cost:{job.pk}", self.buttons(self.tg.last("sendVoice")))
        self.assertIn("18 characters", self.tap(f"out:cost:{job.pk}", message_id=1))

    def test_a_request_being_made(self):
        job = Job.objects.create(account=self.account, kind="chat", model=luna(), chat_id=42,
                                 status=Status.RUNNING, hold=4)
        self.assertIn("Being made: 💎 4 held.", self.tap(f"out:cost:{job.pk}", message_id=1))
