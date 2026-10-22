"""
The core and the presentation: each provider's adapter says what it does,
users choose a provider, and the bot offers only what theirs does. Nothing
is ever done by another provider instead.
"""

from unittest import mock

from django.test import TestCase

from apps.ai import adapters, clients
from apps.ai.adapters.base import Adapter, Made
from apps.ai.models import AIModel, Provider, ProviderKind, Use
from apps.core import runtime
from apps.studio import offer
from apps.studio.models import Conversation, Job

from .base import StudioTestCase

NOT_IMAGES = ("Not offered with Anthropic: Images. You can change the provider in 👤 Profile "
              "→ 🧠 AI provider.")


class PixelAdapter(Adapter):
    """A provider of the future that only makes images."""

    kind = "pixel"
    name = "Pixel"
    default_url = "https://api.pixel.example"
    functions = frozenset({"image"})

    def __init__(self):
        self.made = []

    def image(self, model, prompt, size, quality, photos):
        self.made.append(prompt)
        return Made(b"PNG-pixel", 50, 0, 500)


def anthropic(account=None):
    provider = Provider.objects.get(kind="anthropic")
    Provider.objects.filter(pk=provider.pk).update(api_key="sk-ant")
    provider.refresh_from_db()
    if account is not None:
        offer.choose(account, provider)
    return provider


class CoreTests(TestCase):
    def test_every_provider_kind_has_an_adapter(self):
        self.assertEqual(set(ProviderKind.values), set(adapters.ADAPTERS))
        for adapter in adapters.ADAPTERS.values():
            self.assertTrue(set(adapter.functions) <= set(Use.values), adapter.kind)

    def test_what_each_provider_offers(self):
        Provider.objects.update(api_key="sk-test")
        offers = {provider.kind: provider.offers() for provider in Provider.for_users()}
        self.assertEqual(offers, {"anthropic": ["chat"],
                                  "openai": ["chat", "image", "transcription", "speech"]})

    def test_a_model_its_provider_cannot_run_is_never_offered(self):
        Provider.objects.update(api_key="sk-test")
        AIModel.objects.filter(model_id="claude-haiku-5-5").update(use=Use.IMAGE)
        anthropic_ = Provider.objects.get(kind="anthropic")
        self.assertEqual(AIModel.offered_for(Use.IMAGE, provider=anthropic_), [])
        self.assertEqual(anthropic_.offers(), ["chat"])

    def test_the_core_refuses_what_an_adapter_does_not_do(self):
        Provider.objects.update(api_key="sk-test")
        haiku = AIModel.objects.get(model_id="claude-haiku-5-5")
        for call, args in ((clients.image, ("x", "1024x1024", "low")),
                           (clients.transcribe, (b"OggS", "v.ogg", 5)),
                           (clients.speak, ("Hello", "marin"))):
            with mock.patch("requests.post") as post:
                with self.assertRaisesMessage(clients.AIError, "Anthropic does not"):
                    call(haiku, *args)
            post.assert_not_called()

    def test_a_default_for_users_is_one_per_use_and_provider(self):
        haiku = AIModel.objects.get(model_id="claude-haiku-5-5")
        haiku.is_default = True
        haiku.save()
        self.assertTrue(AIModel.objects.get(model_id="gpt-6-luna").is_default)
        sonnet = AIModel.objects.get(model_id="claude-sonnet-5-5")
        sonnet.is_default = True
        sonnet.save()
        self.assertFalse(AIModel.objects.get(model_id="claude-haiku-5-5").is_default)

    def test_the_default_provider_setting_is_checked(self):
        self.assertEqual(runtime.validate("DEFAULT_PROVIDER", "anthropic"), "")
        self.assertIn("One of: openai, anthropic", runtime.validate("DEFAULT_PROVIDER", "x"))


class AnthropicUserTests(StudioTestCase):
    """Anthropic chats; it makes no images and has no voice, so none is offered."""

    def setUp(self):
        super().setUp()
        anthropic(self.account)
        self.tap("menu:home")

    def test_the_menu_has_only_what_anthropic_does(self):
        self.assertEqual(self.buttons()[:2], ["chat:new", "ag:"])
        self.assertNotIn("img:", self.buttons())
        self.assertNotIn("voice:", self.buttons())
        text = self.last_text()
        self.assertIn("AI provider: Anthropic", text)
        self.assertIn("Model: Claude Haiku 5.5", text)
        self.assertIn("AI tools, right here in Telegram.", text)

    def test_its_chat_is_answered_by_anthropic(self):
        self.say("Hi")
        sent = self.openai.sent("messages")[-1]
        self.assertEqual(sent["json"]["model"], "claude-haiku-5-5")
        job = Job.objects.get()
        self.assertEqual(job.model.model_id, "claude-haiku-5-5")
        self.assertNotIn("out:say", " ".join(self.buttons(self.last_payload())))

    def test_only_its_own_models_to_choose(self):
        self.tap("chat:models")
        models = [data for data in self.buttons() if data.startswith("chat:model:")]
        self.assertEqual(len(models), 2)
        luna = AIModel.objects.get(model_id="gpt-6-luna")
        self.tap(f"chat:model:{luna.pk}")
        self.assertIn("Claude", self.last_text())

    def test_tools_it_does_not_offer_say_so(self):
        self.say("/images")
        self.assertEqual(self.last_text(), NOT_IMAGES)
        self.assertEqual(self.tap("img:create"), NOT_IMAGES)
        self.assertEqual(self.tg.last("answerCallbackQuery")["show_alert"], "true")
        self.assertEqual(self.buttons()[:2], ["chat:new", "ag:"])
        self.say("/voice")
        self.assertIn("Not offered with Anthropic: Voice.", self.last_text())
        self.send_voice()
        self.assertIn("Not offered with Anthropic: Voice to text.", self.last_text())
        self.assertFalse(Job.objects.exists())
        self.assertEqual(self.openai.calls, [])

    def test_the_help_is_its_own(self):
        self.tap("menu:help")
        text = self.last_text()
        self.assertIn("You can send photos, PDFs, text and code files too", text)
        self.assertNotIn("Images:", text)
        self.assertNotIn("Voice:", text)
        self.assertIn("🧠 AI provider", text)

    def test_an_agent_with_another_provider_s_model_uses_the_user_s(self):
        from apps.studio.models import Assistant

        Assistant.objects.filter(slug="chef").update(
            model=AIModel.objects.get(model_id="gpt-6.1-sol"))
        self.tap("ag:start:chef")
        self.say("Pasta?")
        self.assertEqual(self.openai.sent("messages")[-1]["json"]["model"], "claude-haiku-5-5")


class ChoosingTests(StudioTestCase):
    def test_one_provider_no_choice(self):
        self.tap("menu:profile")
        self.assertNotIn("ai:", self.buttons())
        self.assertNotIn("AI provider", self.last_text())

    def test_choosing_a_provider(self):
        anthropic()
        self.tap("menu:profile")
        self.assertIn("🧠 AI provider: OpenAI", self.button_texts())
        self.tap("ai:")
        text = self.last_text()
        self.assertIn("✓ <b>OpenAI</b>\n💬 Chat · 🎨 Images · 📝 Voice to text · 🔊 Text to "
                      "speech", text)
        self.assertIn("<b>Anthropic</b>\n💬 Chat", text)
        self.assertEqual(self.buttons()[:2], ["ai:set:anthropic", "ai:set:openai"])
        self.assertEqual(self.tap("ai:set:anthropic"), "🧠 Anthropic")
        self.assertIn("✓ <b>Anthropic</b>", self.last_text())
        self.assertEqual(offer.provider_of(self.account).kind, "anthropic")

    def test_a_chat_goes_on_with_the_new_provider(self):
        self.say("Hi")
        anthropic()
        self.tap("ai:set:anthropic")
        self.say("And then?")
        sent = self.openai.sent("messages")[-1]["json"]
        self.assertEqual(sent["model"], "claude-haiku-5-5")
        self.assertEqual([message["role"] for message in sent["messages"]],
                         ["user", "assistant", "user"])
        self.assertEqual(Conversation.objects.count(), 1)

    def test_what_was_being_asked_for_is_dropped(self):
        anthropic()
        self.tap("img:")
        self.tap("img:create")
        self.tap("ai:set:anthropic")
        self.say("A red bicycle")
        self.assertEqual(Job.objects.get().kind, "chat")
        self.assertEqual(self.openai.sent("images/generations"), [])

    def test_a_provider_that_goes_away_leaves_the_default(self):
        anthropic(self.account)
        Provider.objects.filter(kind="anthropic").update(api_key="")
        self.assertEqual(offer.provider_of(self.account).kind, "openai")

    @runtime.override(DEFAULT_PROVIDER="anthropic")
    def test_the_default_provider_is_the_owner_s(self):
        anthropic()
        self.assertEqual(offer.provider_of(self.account).kind, "anthropic")
        self.tap("menu:home")
        self.assertNotIn("img:", self.buttons())


class ImageOnlyProviderTests(StudioTestCase):
    """A provider that makes images and does not chat: it is adapted to the same way."""

    def setUp(self):
        super().setUp()
        self.pixel = PixelAdapter()
        patcher = mock.patch.dict(adapters.ADAPTERS, {"pixel": self.pixel})
        patcher.start()
        self.addCleanup(patcher.stop)
        provider = Provider.objects.create(kind="pixel", api_key="px-key")
        self.model = AIModel.objects.create(provider=provider, model_id="pixel-1",
                                            label="Pixel One", use=Use.IMAGE, offered=True,
                                            output_price=10, input_price=1)
        offer.choose(self.account, provider)
        self.tap("menu:home")

    def test_only_images_are_offered(self):
        self.assertEqual(self.buttons()[0], "img:")
        for data in ("chat:new", "ag:", "voice:"):
            self.assertNotIn(data, self.buttons())
        self.tap("img:")
        self.assertEqual(self.buttons()[0], "img:create")
        self.assertNotIn("img:edit", self.buttons())

    def test_typing_says_chat_is_not_offered(self):
        self.say("Hello?")
        self.assertEqual(self.last_text(), "Not offered with pixel: Chat. You can change the "
                                           "provider in 👤 Profile → 🧠 AI provider.")
        self.assertFalse(Job.objects.exists())

    def test_an_image_is_made_by_it(self):
        self.tap("img:create")
        self.say("A red bicycle")
        self.tap("img:go")
        job = Job.objects.get()
        self.assertEqual(job.model, self.model)
        self.assertEqual(self.pixel.made, ["A red bicycle"])
        self.assertEqual(self.openai.sent("images/generations"), [])
        markup = self.last_payload("sendPhoto")
        self.assertNotIn(f"out:edit:{job.pk}", self.buttons(markup))
        self.assertIn(f"out:again:{job.pk}", self.buttons(markup))
