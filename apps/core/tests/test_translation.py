"""
AI translation of the bot's texts: what is sent, what is accepted, and the
status the admin sees whatever happens.
"""
import json
from unittest import mock

from django.test import TestCase

from apps.ai import clients
from apps.ai.models import AIModel, Provider
from apps.core import tasks, translation
from apps.core.models import ActivityLog, Language, SourceText, Translation, TranslationStatus
from apps.core.services import budget


def fake_ai(translate=lambda text: f"[de] {text}", answers=None):
    """Stands in for clients.ask: translates every numbered text it is sent."""
    calls = []

    def ask(model, system, prompt, purpose, max_tokens=4000):
        numbered = json.loads(prompt)
        calls.append({"model": model.model_id, "system": system, "texts": numbered})
        body = answers if answers is not None else {
            key: translate(text) for key, text in numbered.items()}
        return clients.Reply(json.dumps(body, ensure_ascii=False), 100, 50, 35)

    return ask, calls


class TranslationRunTests(TestCase):
    def setUp(self):
        Provider.objects.filter(kind="openai").update(api_key="sk-openai-test")
        self.total = SourceText.objects.filter(active=True).count()

    def run_with(self, ask, code="de", **options):
        with mock.patch.object(clients, "ask", ask):
            return translation.run(code, **options)

    def test_every_missing_text_is_translated_with_the_default_model(self):
        ask, calls = fake_ai()
        self.assertEqual(self.run_with(ask), TranslationStatus.DONE)
        self.assertEqual(Translation.objects.filter(language="de").count(), self.total)
        self.assertEqual(calls[0]["model"], "gpt-6-luna")
        self.assertIn("Deutsch (German, language code de)", calls[0]["system"])
        self.assertIn("AIstars, a Telegram bot where people chat with AI", calls[0]["system"])
        german = Language.objects.get(code="de")
        self.assertEqual(german.status_detail,
                         f"{self.total} of {self.total} translated with GPT-6 Luna")
        self.assertIsNotNone(german.translated_at)
        self.assertEqual(Translation.objects.filter(language="de").first().model, "gpt-6-luna")

    def test_only_missing_texts_are_sent(self):
        from .helpers import complete_translations

        complete_translations("ro")
        Translation.objects.filter(language="ro", source__text="Help").delete()
        ask, calls = fake_ai()
        self.run_with(ask, code="ro")
        self.assertEqual(list(calls[0]["texts"].values()), ["Help"])

    def test_texts_go_in_batches(self):
        for index in range(translation.BATCH + 5):
            SourceText.objects.create(text=f"Extra text {index}")
        ask, calls = fake_ai()
        self.run_with(ask)
        total = self.total + translation.BATCH + 5
        sizes = [min(translation.BATCH, total - start)
                 for start in range(0, total, translation.BATCH)]
        self.assertEqual([len(call["texts"]) for call in calls], sizes)

    def test_retranslating_keeps_hand_made_fixes(self):
        from .helpers import seed_translations

        seed_translations()
        Translation.objects.filter(language="ro", source__text="Help").update(
            text="Ajutor!", manual=True)
        ask, calls = fake_ai(lambda text: f"nou {text}")
        self.run_with(ask, code="ro", overwrite=True)
        self.assertEqual(Translation.objects.get(language="ro", source__text="Help").text,
                         "Ajutor!")
        self.assertEqual(Translation.objects.get(language="ro", source__text="Profile").text,
                         "nou Profile")

    def test_retranslating_can_replace_hand_made_fixes_too(self):
        from .helpers import seed_translations

        seed_translations()
        Translation.objects.filter(language="ro", source__text="Help").update(manual=True)
        ask, _ = fake_ai(lambda text: f"nou {text}")
        self.run_with(ask, code="ro", overwrite=True, overwrite_manual=True)
        row = Translation.objects.get(language="ro", source__text="Help")
        self.assertEqual((row.text, row.manual), ("nou Help", False))

    def test_another_model_can_be_chosen(self):
        sol = AIModel.objects.get(model_id="gpt-6.1-sol")
        ask, calls = fake_ai()
        self.run_with(ask, model_id=sol.pk)
        self.assertEqual(calls[0]["model"], "gpt-6.1-sol")

    def test_a_language_can_have_its_own_model(self):
        Language.objects.filter(code="de").update(
            translator=AIModel.objects.get(model_id="gpt-6.1-sol"))
        ask, calls = fake_ai()
        self.run_with(ask)
        self.assertEqual(calls[0]["model"], "gpt-6.1-sol")

    def test_bad_answers_stay_english_and_are_reported(self):
        def answer(text):
            if text == "Help":
                return "<b>Hilfe</b>"
            if text == "Profile":
                return ""
            return f"[de] {text}"

        ask, _ = fake_ai(answer)
        self.assertEqual(self.run_with(ask), TranslationStatus.DONE)
        german = Language.objects.get(code="de")
        self.assertFalse(Translation.objects.filter(language="de", source__text="Help").exists())
        self.assertIn("“Help”: contains HTML", german.status_detail)
        self.assertIn("“Profile”: empty", german.status_detail)

    def test_an_answer_that_is_not_json_fails_the_run(self):
        def ask(model, system, prompt, purpose, max_tokens=4000):
            return clients.Reply("Sorry, I cannot.", 10, 5, 1)

        self.assertEqual(self.run_with(ask), TranslationStatus.FAILED)
        self.assertIn("did not answer with JSON", Language.objects.get(code="de").status_detail)
        self.assertTrue(ActivityLog.objects.filter(event="translation_failed").exists())

    def test_without_an_api_key_it_fails_with_the_fix(self):
        Provider.objects.update(api_key="")
        ask, calls = fake_ai()
        self.assertEqual(self.run_with(ask), TranslationStatus.FAILED)
        self.assertEqual(calls, [])
        self.assertIn("AI → Providers", Language.objects.get(code="de").status_detail)

    def test_without_a_default_model_it_fails_with_the_fix(self):
        AIModel.objects.update(default_for_translation=False)
        ask, _ = fake_ai()
        self.assertEqual(self.run_with(ask), TranslationStatus.FAILED)
        self.assertIn("AI → Models", Language.objects.get(code="de").status_detail)

    def test_the_kill_switch_stops_it_and_keeps_what_was_done(self):
        for index in range(translation.BATCH):
            SourceText.objects.create(text=f"Extra text {index}")
        ask, _ = fake_ai()
        real_ask = ask
        calls = []

        def ask_then_pause(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise budget.PlatformPaused("paused in the admin")
            return real_ask(*args, **kwargs)

        self.assertEqual(self.run_with(ask_then_pause), TranslationStatus.FAILED)
        self.assertEqual(Translation.objects.filter(language="de").count(), translation.BATCH)
        self.assertIn(f"{translation.BATCH} of", Language.objects.get(code="de").status_detail)

    def test_english_is_never_translated(self):
        ask, calls = fake_ai()
        self.assertEqual(self.run_with(ask, code="en"), TranslationStatus.FAILED)
        self.assertEqual(calls, [])


class CheckTests(TestCase):
    def test_placeholders_must_survive(self):
        self.assertEqual(translation.check("Hi %(name)s", "Salut %(name)s"), "")
        self.assertEqual(translation.check("Hi %(name)s", "Salut"), "placeholders changed")

    def test_runaway_text_is_refused(self):
        self.assertEqual(translation.check("OK", "x" * 100), "far too long")

    def test_a_code_fence_around_the_json_is_fine(self):
        self.assertEqual(translation.parse_reply('```json\n{"1": "a"}\n```'), {"1": "a"})


class TaskTests(TestCase):
    def setUp(self):
        Provider.objects.filter(kind="openai").update(api_key="sk-openai-test")

    def test_a_running_language_is_not_started_twice(self):
        from django.core.cache import cache

        cache.add("i18n:translating:de", 1, 60)
        self.addCleanup(cache.delete, "i18n:translating:de")
        self.assertEqual(tasks.translate_language("de"), "already running")

    def test_missing_texts_are_picked_up_for_languages_with_translate_on(self):
        SourceText.objects.create(text="Something new")
        Language.objects.filter(code="de").update(translate=True)
        ask, calls = fake_ai()
        with mock.patch.object(clients, "ask", ask), \
                self.captureOnCommitCallbacks(execute=True):
            started = tasks.translate_missing()
        self.assertEqual(sorted(started), ["de", "ro", "ru"])
        self.assertTrue(Translation.objects.filter(language="ro",
                                                   source__text="Something new").exists())
        self.assertEqual(Language.objects.get(code="de").status, TranslationStatus.DONE)

    def test_without_a_usable_model_nothing_runs_and_the_status_says_why(self):
        from apps.core.models import ActivityLog

        Provider.objects.update(api_key="")
        SourceText.objects.create(text="Something new")
        ActivityLog.objects.all().delete()
        self.assertEqual(tasks.translate_missing(), [])
        romanian = Language.objects.get(code="ro")
        self.assertEqual(romanian.status, TranslationStatus.FAILED)
        self.assertIn("AI → Providers", romanian.status_detail)
        self.assertFalse(ActivityLog.objects.exists())

    def test_nothing_is_started_when_nothing_is_missing(self):
        from .helpers import complete_translations

        complete_translations("ro", "ru")
        self.assertEqual(tasks.translate_missing(), [])
