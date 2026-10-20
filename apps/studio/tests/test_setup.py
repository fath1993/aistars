"""
What the owner sets up: the archive channel, the texts from the database
that users read (translated like the bot's own), and the doctor's AI tools
section.
"""
from django.test import TestCase
from django.utils import translation

from apps.ai.models import AIModel, Provider
from apps.core import diagnostics, i18n, runtime
from apps.core.models import ControlPanel, SourceText, Translation
from apps.core.tests.helpers import FakeTelegram, register
from apps.studio import archive, catalog
from apps.studio.models import Assistant

OWNER = 555000111
CHANNEL = -1001234567890


@runtime.override(ADMIN_CHAT_ID=OWNER)
class ArchiveTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        self.addCleanup(cache.clear)   # the channel is cached with the runtime settings
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def update(self, status, by=OWNER, can_post=True):
        return {"chat": {"id": CHANNEL, "type": "channel", "title": "AIstars archive"},
                "from": {"id": by},
                "new_chat_member": {"status": status, "can_post_messages": can_post}}

    def test_the_owner_makes_the_bot_admin_of_a_channel(self):
        archive.on_member_update(self.update("administrator"))
        self.assertEqual(archive.chat_id(), CHANNEL)
        self.assertIn("is now the archive channel", self.tg.last("sendMessage")["text"])

    def test_anyone_else_is_ignored(self):
        archive.on_member_update(self.update("administrator", by=999))
        self.assertIsNone(archive.chat_id())

    def test_a_channel_it_may_not_post_in(self):
        archive.on_member_update(self.update("administrator", can_post=False))
        self.assertIsNone(archive.chat_id())
        self.assertIn("may not post", self.tg.last("sendMessage")["text"])

    def test_telegram_reports_it_through_the_webhook(self):
        from apps.core.tests.helpers import configured, post_update

        with configured:
            post_update({"update_id": 1, "my_chat_member": self.update("administrator")})
        self.assertEqual(archive.chat_id(), CHANNEL)

    def test_losing_it_clears_it_and_says_so(self):
        archive.on_member_update(self.update("administrator"))
        archive.on_member_update(self.update("left"))
        self.assertIsNone(archive.chat_id())
        self.assertIn("nothing is archived", self.tg.last("sendMessage")["text"])


class CatalogTests(TestCase):
    def test_taglines_and_assistants_are_recorded_for_translation(self):
        texts = set(SourceText.objects.filter(location__startswith=i18n.CATALOG)
                    .values_list("text", flat=True))
        self.assertIn("Fast and cheap, for everyday questions", texts)
        self.assertIn("Translator", texts)
        self.assertIn("Send the text, and say which language to translate it into.", texts)

    def test_an_edit_in_the_admin_is_recorded(self):
        tutor = Assistant.objects.get(slug="tutor")
        tutor.description = "Explains anything, slowly."
        tutor.save()
        self.assertTrue(SourceText.objects.filter(text="Explains anything, slowly.",
                                                  active=True).exists())
        self.assertFalse(SourceText.objects.get(
            text="Explains any topic step by step, at your level.").active)

    def test_categories_and_starters_are_recorded(self):
        texts = dict(SourceText.objects.filter(location__startswith=i18n.CATALOG)
                     .values_list("text", "location"))
        self.assertEqual(texts["Law"], "catalog:category:law")
        self.assertEqual(texts["Explain photosynthesis simply"], "catalog:agent:tutor")

    def test_users_read_them_in_their_language(self):
        source = SourceText.objects.get(text="Translator")
        Translation.objects.create(language_id="ro", source=source, text="Traducător")
        with translation.override("ro"):
            self.assertEqual(catalog.tr("Translator"), "Traducător")
            self.assertEqual(catalog.tr(""), "")


class DoctorTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()   # runtime settings cached by an earlier test

    def labels(self):
        return {check.label: check for check in diagnostics.run(include_telegram=False).checks
                if check.section == "AI tools"}

    def test_demo_mode_with_a_tester(self):
        register(42, tester=True)
        labels = self.labels()
        self.assertEqual(labels["Using the tools"].detail,
                         "Demo mode (no provider is called), for 1 tester(s) only")
        self.assertEqual(labels["Chat"].detail,
                         "GPT-6 Luna (default), GPT-6.1 Sol, GPT-6 Astra, Claude Haiku 5.5, "
                         "Claude Sonnet 5.5")
        self.assertEqual(labels["Agents"].detail, "39 on offer in 10 categories")
        self.assertNotIn("Agents without a category", labels)
        self.assertEqual(labels["Archive channel"].status, diagnostics.WARN)

    def test_live_without_a_key_nothing_shows(self):
        ControlPanel.objects.update_or_create(singleton=1, defaults={"demo_mode": False,
                                                                     "open_to_everyone": True})
        labels = self.labels()
        self.assertEqual(labels["Using the tools"].detail,
                         "Live, with the providers, open to everyone")
        for name in ("Chat", "Images", "Voice to text", "Text to speech"):
            self.assertEqual(labels[name].status, diagnostics.WARN, name)
            self.assertIn("AI → Providers → OpenAI", labels[name].fix)
        Provider.objects.filter(kind="openai").update(api_key="sk-test")
        labels = self.labels()
        self.assertEqual(labels["Images"].detail,
                         "GPT Image 2.5 Flare (default), GPT Image 2.5 Sunburst")
        self.assertEqual(labels["Chat"].status, diagnostics.OK)

    def test_nobody_can_use_them_without_a_tester(self):
        self.assertEqual(self.labels()["Using the tools"].status, diagnostics.WARN)

    def test_an_agent_in_no_category(self):
        Assistant.objects.filter(slug="tutor").update(category=None)
        self.assertIn("1 agent(s) on offer are in no category",
                      self.labels()["Agents without a category"].detail)

    def test_an_offered_model_the_provider_does_not_list(self):
        AIModel.objects.filter(model_id="gpt-6-astra").update(listed=False)
        self.assertIn("1 offered model(s)", self.labels()["Models not at the provider"].detail)
