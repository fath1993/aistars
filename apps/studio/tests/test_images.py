"""
Images through the webhook: a description (and a photo) is checked by all
three safety layers before its price shows; Create holds the most it can
cost and charges what OpenAI says it used; the image comes with Again, Edit
this and the original file.
"""
from apps.ai.models import AIModel
from apps.core import runtime
from apps.core.models import Account
from apps.safety.models import Layer, Result, Screening
from apps.studio import images
from apps.studio.models import Job, Status

from .base import StudioTestCase

ARCHIVE = -1001234567890


class CreateTests(StudioTestCase):
    def describe(self, text="A red fox in the snow at sunrise"):
        self.tap("img:")
        self.tap("img:create")
        self.say(text)

    def test_the_images_screen(self):
        self.tap("img:")
        self.assertIn("Model: <b>GPT Image 2.5 Flare</b>", self.last_text())
        self.assertEqual(self.buttons(), ["img:create", "img:edit", "img:models", "menu:home",
                                          "menu:home"])

    def test_a_description_is_checked_then_priced(self):
        self.describe()
        self.assertEqual(self.openai.paths(), ["moderations", "chat/completions"])
        screening = Screening.objects.get()
        self.assertEqual((screening.tool, screening.result), ("image", Result.PASSED))
        self.assertEqual(list(screening.checks.values_list("layer", flat=True)),
                         [Layer.BLOCKLIST, Layer.MODERATION, Layer.JUDGE])
        text = self.last_text()
        self.assertIn("<i>A red fox in the snow at sunrise</i>", text)
        self.assertIn("Shape: ⬜ Square · Quality: Good", text)
        hold = images.estimate(AIModel.objects.get(model_id="gpt-image-2.5-flare"),
                               "A red fox in the snow at sunrise", "1024x1024", "medium", [])
        self.assertIn("Price: <b>at most 🪙 ", text)
        self.assertIn("You pay what it really costs, never more.", text)
        self.assertIn("img:go", self.buttons())
        self.assertGreater(hold, 0)

    def test_options_change_the_price_without_a_new_check(self):
        self.describe()
        low = self.last_text()
        self.tap("img:quality:high")
        self.tap("img:size:1536x1024")
        self.assertIn("Shape: ▭ Landscape · Quality: Best", self.last_text())
        self.assertNotEqual(low, self.last_text())
        self.assertEqual(Screening.objects.count(), 1)

    def test_create_makes_it_and_charges_what_it_used(self):
        self.describe()
        self.tap("img:quality:high")
        self.tap("img:go")
        job = Job.objects.get()
        self.assertEqual((job.kind, job.status, job.options),
                         ("image", Status.DONE, {"size": "1024x1024", "quality": "high"}))
        body = self.openai.sent("images/generations")[0]["json"]
        self.assertEqual((body["model"], body["size"], body["quality"]),
                         ("gpt-image-2.5-flare", "1024x1024", "high"))
        # 100 x $5 + 1000 x $8 + 1056 x $30 per 1M = $0.04018: 🪙 5 at a 40% margin.
        self.assertEqual((job.cost_micros, job.charged), (40_180, 5))
        self.assertLess(job.charged, job.hold)
        self.assertEqual(self.balance(), 495)
        photo = self.tg.last("sendPhoto")
        self.assertEqual(photo["chat_id"], 42)
        self.assertIn(f"🎨 #{job.pk}", photo["caption"])
        self.assertIn("🪙 5 · Balance 🪙 495", photo["caption"])
        self.assertEqual(self.buttons(photo), [f"out:again:{job.pk}", f"out:edit:{job.pk}",
                                               "menu:new"])
        self.assertTrue(job.result_file_id)

    def test_the_panel_says_it_is_being_made(self):
        self.describe()
        self.tap("img:go")
        job = Job.objects.get()
        self.assertIn(f"Making your image (#{job.pk})",
                      [text for text in self.messages() if "Making" in text][-1])

    @runtime.override(ARCHIVE_CHAT_ID=ARCHIVE)
    def test_with_the_archive_the_original_file_is_kept(self):
        self.describe()
        self.tap("img:go")
        job = Job.objects.get()
        archived = [(method, payload["caption"]) for method, payload in self.tg.calls
                    if payload.get("chat_id") == ARCHIVE]
        self.assertEqual(archived, [("sendPhoto", f"#J{job.pk} · user 42 · output"),
                                    ("sendDocument", f"#J{job.pk} · user 42 · original")])
        photo = [payload for method, payload in self.tg.calls
                 if method == "sendPhoto" and payload["chat_id"] == 42][-1]
        self.assertIn(f"out:file:{job.pk}", self.buttons(photo))
        self.tap(f"out:file:{job.pk}", message_id=777)
        self.assertEqual(self.tg.last("sendDocument")["document"], job.original_file_id)

    def test_again_brings_the_same_request_back(self):
        self.describe()
        self.tap("img:size:1024x1536")
        self.tap("img:go")
        job = Job.objects.get()
        self.tap(f"out:again:{job.pk}", message_id=777)
        self.assertIn("Shape: ▯ Portrait", self.last_text())
        self.tap("img:go")
        self.assertEqual(Job.objects.count(), 2)

    def test_a_new_description_on_the_summary_replaces_it(self):
        self.describe()
        self.say("A blue whale instead")
        self.assertIn("<i>A blue whale instead</i>", self.last_text())
        self.assertEqual(Screening.objects.count(), 2)

    def test_a_blocked_description_shows_why_and_nothing_is_made(self):
        self.describe("A naked person")
        self.assertIn("This is not allowed: sexual content.", self.last_text())
        self.assertEqual(self.buttons(), ["img:", "menu:home"])
        self.assertNotIn("img:go", self.buttons())
        self.assertEqual(Account.objects.get(pk=self.account.pk).strikes, 1)
        self.tap("img:go")
        self.assertFalse(Job.objects.exists())

    def test_the_judge_blocks_what_the_policy_forbids(self):
        self.openai.judge = '{"verdict": "block", "category": "real_person", "reason": "x"}'
        self.describe("The president riding a horse")
        self.assertIn("This is not allowed: a real person.", self.last_text())

    def test_without_a_judge_nothing_is_made(self):
        self.openai.fail["chat/completions"] = (500, "down")
        self.describe()
        self.assertIn("could not be checked right now", self.last_text())
        self.assertEqual(Account.objects.get(pk=self.account.pk).strikes, 0)

    def test_openais_own_refusal_is_a_strike_and_costs_nothing(self):
        self.describe()
        self.openai.fail["images/generations"] = (451, "rejected by the safety system")
        self.tap("img:go")
        job = Job.objects.get()
        self.assertEqual((job.status, self.balance()), (Status.FAILED, 500))
        self.assertIn("The AI provider's own safety check refused this", self.last_text())
        refused = Screening.objects.get(layer=Layer.PROVIDER)
        self.assertEqual((refused.job_number, refused.strike), (job.pk, True))
        self.assertEqual(Account.objects.get(pk=self.account.pk).strikes, 1)

    def test_a_failure_returns_everything(self):
        self.describe()
        self.openai.fail["images/generations"] = (500, "server error")
        self.tap("img:go")
        job = Job.objects.get()
        self.assertEqual((job.status, self.balance()), (Status.FAILED, 500))
        self.assertIn("could not be made, so nothing was charged", self.last_text())
        self.assertEqual(self.buttons(), [f"out:again:{job.pk}", "menu:new"])

    def test_a_result_telegram_takes_nowhere_costs_nothing(self):
        self.describe()
        self.tg.fail.add("sendPhoto")
        self.tap("img:go")
        self.assertEqual((Job.objects.get().status, self.balance()), (Status.FAILED, 500))

    def test_cancel_and_old_buttons(self):
        self.tap("img:")
        self.tap("img:create")
        self.tap("img:")
        self.say("Just chatting now")
        self.assertEqual(Job.objects.get().kind, "chat")
        self.assertEqual(self.tap("img:go"), "That button has expired.")


class LowCreditTests(StudioTestCase):
    credits = 2

    def test_not_enough_credits_offers_to_buy(self):
        self.tap("img:")
        self.tap("img:create")
        self.say("A red fox")
        self.tap("img:quality:high")
        self.assertIn("credits:buy", self.buttons())
        self.assertNotIn("img:go", self.buttons())


class EditTests(StudioTestCase):
    def test_a_photo_is_changed(self):
        self.tap("img:")
        self.tap("img:edit")
        self.assertIn("Send the photo you want to change.", self.last_text())
        self.send_photo("photo-7")
        self.assertIn("What should change?", self.last_text())
        self.say("Make it night time")
        screening = Screening.objects.get()
        self.assertEqual((screening.tool, screening.photos),
                         ("edit", [{"file_id": "photo-7", "kind": "photo"}]))
        self.assertIn("Your photo edit", self.last_text())
        self.assertIn("Shape: 🪄 As the photo", self.last_text())
        self.tap("img:go")
        job = Job.objects.get()
        self.assertEqual((job.kind, job.photos, job.status), ("edit", ["photo-7"], Status.DONE))
        edit = self.openai.sent("images/edits")[0]
        self.assertEqual(edit["data"]["size"], "auto")
        self.assertEqual(edit["files"][0][0], "image")
        self.assertEqual(Job.objects.filter(kind="chat").count(), 0,
                         "the photo went to the edit, not to the chat")

    def test_a_photo_with_its_caption_goes_straight_to_the_summary(self):
        self.tap("img:")
        self.tap("img:edit")
        self.send_photo("photo-8", caption="Add a rainbow")
        self.assertIn("<i>Add a rainbow</i>", self.last_text())

    def test_editing_the_image_just_made(self):
        self.tap("img:")
        self.tap("img:create")
        self.say("A cat")
        self.tap("img:go")
        made = Job.objects.get()
        self.tap(f"out:edit:{made.pk}", message_id=777)
        self.say("Give it a hat")
        self.tap("img:go")
        edit = Job.objects.get(kind="edit")
        self.assertEqual(edit.photos, [made.result_file_id])

    def test_a_model_that_cannot_change_photos_is_not_used_for_edits(self):
        AIModel.objects.filter(use="image").update(sees_images=False)
        self.tap("img:")
        self.assertNotIn("img:edit", self.buttons())


class ImageModelTests(StudioTestCase):
    def test_choosing_the_image_model(self):
        sunburst = AIModel.objects.get(model_id="gpt-image-2.5-sunburst")
        self.tap("img:models")
        self.assertIn("Sunburst", self.last_text())
        self.assertEqual(self.tap(f"img:model:{sunburst.pk}"), "GPT Image 2.5 Sunburst")
        self.tap("img:create")
        self.say("A cat")
        self.tap("img:go")
        self.assertEqual(Job.objects.get().model, sunburst)


class EstimateTests(StudioTestCase):
    def test_the_hold_covers_the_biggest_size_and_the_photos(self):
        model = AIModel.objects.get(model_id="gpt-image-2.5-flare")
        square = images.estimate(model, "x", "1024x1024", "high", [])
        auto = images.estimate(model, "x", "auto", "high", ["photo"])
        self.assertGreater(auto, square)
        # 4,160 image tokens at $30 per 1M, half again to spare.
        self.assertGreaterEqual(square, int(4160 * 30 * 1.5))
