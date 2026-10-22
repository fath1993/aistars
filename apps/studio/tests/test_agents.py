"""
The agent catalog through the webhook: browsing categories, an agent's card,
starting a chat with it or with one of its questions, saving and sharing it,
searching, the AI finding agents for a need, and the way back from anywhere.
"""
from unittest import mock

from apps.ai.models import AIModel
from apps.core.models import Account, ControlPanel
from apps.core.tests.helpers import contact_update, message_update, post_update
from apps.studio import agents, chat
from apps.studio.models import Assistant, Category, Conversation, Job, Message

from .base import StudioTestCase


class CatalogTests(StudioTestCase):
    def test_home_leads_to_the_agents(self):
        self.say("/menu")
        self.assertIn("Just type to chat, or pick an agent", self.last_text())
        self.assertEqual(self.buttons()[:4], ["chat:new", "ag:", "img:", "voice:"])
        self.say("Hi")
        self.say("/menu")
        self.assertEqual(self.buttons()[:2], ["chat:new", "chat:"], "▶️ Continue")

    def test_the_agents_screen(self):
        self.tap("ag:")
        text = self.last_text()
        self.assertIn("🧩 <b>Agents</b>", text)
        self.assertIn("39 agents in 10 categories", text)
        buttons = self.buttons()
        self.assertEqual(buttons[:2], ["ag:cat:law", "ag:cat:learning"])
        self.assertIn("ag:search", buttons)
        self.assertIn("ag:find", buttons)
        self.assertIn("ag:top", buttons)
        self.assertNotIn("ag:saved", buttons, "nothing saved yet")

    def test_a_category(self):
        self.tap("ag:cat:law")
        text = self.last_text()
        self.assertIn("🧩 Agents › <b>⚖️ Law</b>", text)
        self.assertIn("🧑‍⚖️ <b>Lawyer</b> — <i>Explains your legal question", text)
        self.assertEqual(self.buttons(), ["ag:card:lawyer", "ag:card:contract", "ag:card:letters",
                                          "ag:card:housing", "ag:", "menu:home"])

    def test_a_long_category_has_pages(self):
        with mock.patch.object(agents, "PER_PAGE", 3):
            self.tap("ag:cat:law")
            self.assertIn("ag:cat:law:1", self.buttons())
            self.tap("ag:cat:law:1")
            self.assertEqual(self.buttons()[:2], ["ag:card:housing", "ag:cat:law:0"])

    def test_an_empty_or_hidden_category_is_not_shown(self):
        Category.objects.filter(slug="law").update(active=False)
        self.tap("ag:")
        self.assertNotIn("ag:cat:law", self.buttons())
        self.assertIn("35 agents in 9 categories", self.last_text())
        self.tap("ag:cat:law")
        self.assertIn("🧩 <b>Agents</b>", self.last_text())

    def test_the_card(self):
        self.tap("ag:cat:law")
        self.tap("ag:card:lawyer")
        text = self.last_text()
        self.assertIn("🧩 Agents › <b>⚖️ Law</b>", text)
        self.assertIn("🧑‍⚖️ <b>Lawyer</b>", text)
        self.assertIn("Try asking:\n• My employer hasn't paid my salary", text)
        self.assertIn("Model: <b>GPT-6.1 Sol</b> · An everyday answer: ~💎 2", text)
        buttons = self.buttons()
        self.assertEqual(buttons[:4], ["ag:start:lawyer", "ag:try:lawyer:0", "ag:try:lawyer:1",
                                       "ag:try:lawyer:2"])
        self.assertEqual(buttons[4], "ag:save:lawyer")
        self.assertTrue(buttons[5].startswith(
            "https://t.me/share/url?url=https%3A%2F%2Ft.me%2FAIstarsBot%3Fstart%3Dagent_lawyer"))
        self.assertEqual(buttons[-2:], ["ag:back", "menu:home"])
        self.tap("ag:back")
        self.assertIn("ag:card:contract", self.buttons(), "back to the category")

    def test_an_agent_no_longer_offered(self):
        Assistant.objects.filter(slug="lawyer").update(active=False)
        self.assertEqual(self.tap("ag:card:lawyer"), "This agent is no longer offered.")
        self.assertIn("🧩 <b>Agents</b>", self.last_text())
        self.assertEqual(self.tap("ag:start:lawyer"), "This agent is no longer offered.")

    def test_the_agents_command(self):
        self.say("/agents")
        self.assertIn("🧩 <b>Agents</b>", self.last_text())


class StartTests(StudioTestCase):
    def test_starting_a_chat_with_an_agent(self):
        self.tap("ag:card:lawyer")
        self.tap("ag:start:lawyer")
        text = self.last_text()
        self.assertIn("💬 <b>Chat</b> · 🧑‍⚖️ Lawyer", text)
        self.assertIn("Which country are you in, and what happened?", text)
        self.assertIn("Or tap a question below to begin.", text)
        self.assertEqual(self.buttons()[:4], ["ag:ask:0", "ag:ask:1", "ag:ask:2", "chat:new"])
        self.assertIn("ag:about:lawyer", self.buttons(), "ℹ️ About the agent")
        self.say("I was fired without notice")
        job = Job.objects.get()
        self.assertEqual(job.model.model_id, "gpt-6.1-sol")
        self.assertEqual(job.assistant.slug, "lawyer")
        self.assertIn("Your role in this chat:\nYou are an experienced lawyer",
                      self.asked()["instructions"])
        self.assertIn("You give general information, not professional advice",
                      self.asked()["instructions"])

    def test_a_starter_question_on_the_chat_screen(self):
        self.tap("ag:start:translator")
        self.tap("ag:ask:0")
        question = "Translate “Thank you for your help” into French, German and Spanish"
        self.assertIn(f"🗨 <i>{question}</i>", self.messages())
        job = Job.objects.get()
        self.assertEqual(job.prompt, question)
        self.assertEqual(Message.objects.filter(role="user").get().text, question)
        self.assertIn("Messages: 2", self.last_text(), "the panel shows the chat now")

    def test_a_starter_question_from_the_card(self):
        self.say("An old chat")
        self.tap("ag:card:tutor")
        self.tap("ag:try:tutor:1")
        job = Job.objects.get(assistant__slug="tutor")
        self.assertEqual(job.prompt, "How does inflation work?")
        self.assertEqual(Conversation.objects.count(), 2, "a new chat with the agent")

    def test_a_starter_that_does_not_exist(self):
        self.tap("ag:try:tutor:9")
        self.assertFalse(Job.objects.exists())
        self.tap("ag:ask:5")
        self.assertFalse(Job.objects.exists())

    def test_an_agent_with_its_own_model(self):
        sol = AIModel.objects.get(model_id="gpt-6.1-sol")
        Assistant.objects.filter(slug="writer").update(model=sol)
        self.tap("ag:start:writer")
        self.say("Fix my essay")
        self.assertEqual(Job.objects.get().model, sol)

    def test_the_new_chat_choice(self):
        self.tap("chat:new")
        buttons = self.buttons()
        self.assertEqual(buttons[0], "chat:general")
        self.assertEqual(buttons[1:7], ["ag:start:lawyer", "ag:start:tutor", "ag:start:code",
                                        "ag:start:translator", "ag:start:partner", "ag:start:cv"])
        self.tap("ag:start:lawyer")
        self.say("Hello")
        self.tap("chat:new")
        self.assertEqual(self.button_texts()[0], "↻ Again with 🧑‍⚖️ Lawyer")
        self.assertEqual(self.buttons()[0], "ag:start:lawyer")

    def test_about_the_agent_goes_back_to_the_chat(self):
        self.tap("ag:start:chef")
        self.tap("ag:about:chef")
        self.assertIn("👨‍🍳 <b>Chef</b>", self.last_text())
        self.tap("ag:back")
        self.assertIn("💬 <b>Chat</b> · 👨‍🍳 Chef", self.last_text())


class SavedTests(StudioTestCase):
    def test_saving_and_my_agents(self):
        self.assertEqual(self.tap("ag:save:chef"), "⭐ Saved to My agents")
        self.assertIn("👨‍🍳 <b>Chef</b> ⭐", self.last_text())
        self.assertIn("ag:save:chef", self.buttons())
        self.assertIn("✓ Saved", self.button_texts())
        self.tap("ag:")
        self.assertIn("ag:saved", self.buttons())
        self.tap("ag:saved")
        self.assertIn("⭐ My agents", self.last_text())
        self.assertEqual(self.buttons()[0], "ag:card:chef")
        self.tap("ag:card:chef")
        self.tap("ag:back")
        self.assertIn("⭐ My agents", self.last_text())
        self.assertEqual(self.tap("ag:save:chef"), "Removed from My agents")
        self.assertEqual(agents.saved(self.account), [])

    def test_my_agents_empty(self):
        self.tap("ag:saved")
        self.assertIn("No saved agents yet", self.last_text())

    def test_recent_and_popular(self):
        self.tap("ag:start:chef")
        self.say("Pasta tonight?")
        self.tap("ag:start:travel")
        self.tap("ag:recent")
        self.assertEqual(self.buttons()[:2], ["ag:card:travel", "ag:card:chef"])
        self.tap("ag:top")
        self.assertIn("The agents people used most this month", self.last_text())
        self.assertEqual(self.buttons()[:2], ["ag:card:chef", "ag:"], "travel: no message yet")


class SearchTests(StudioTestCase):
    def test_searching_by_words(self):
        self.tap("ag:search")
        self.assertIn("Type what you need", self.last_text())
        self.say("contract")
        text = self.last_text()
        self.assertIn("🔍 Search", text)
        self.assertIn("“contract”", text)
        self.assertEqual(self.buttons()[0], "ag:card:contract")
        self.assertFalse(Job.objects.exists(), "the words are not a chat message")
        self.tap(self.buttons()[0])
        self.tap("ag:back")
        self.assertIn("“contract”", self.last_text(), "back to the results")
        self.say("Now this is a chat")
        self.assertEqual(Job.objects.count(), 1)

    def test_nothing_found(self):
        self.tap("ag:search")
        self.say("xylophone tuning")
        self.assertIn("No agent matches these words", self.last_text())
        self.assertIn("ag:find", self.buttons())

    def test_in_the_users_language(self):
        from apps.core.models import SourceText, Translation

        source = SourceText.objects.get(text="Chef")
        Translation.objects.create(language_id="ro", source=source, text="Bucătar")
        Account.objects.filter(pk=self.account.pk).update(language="ro")
        found = None
        from django.utils import translation

        with translation.override("ro"):
            found = agents.search("bucătar")
        self.assertEqual(found[0].slug, "chef")


class FindTests(StudioTestCase):
    def test_the_ai_finds_agents_for_a_need(self):
        self.openai.judge = '{"agents": ["housing", "contract", "made-up"]}'
        self.tap("ag:find")
        self.assertIn("Free. Left today: 20", self.last_text())
        self.say("My landlord wants to keep my whole deposit")
        text = self.last_text()
        self.assertIn("✨ Found for you", text)
        self.assertIn("“My landlord wants to keep my whole deposit”", text)
        self.assertEqual(self.buttons()[:2], ["ag:card:housing", "ag:card:contract"])
        asked = self.openai.sent("responses")[-1]["json"]
        self.assertIn("lawyer: Lawyer, Explains your legal question", asked["instructions"])
        self.assertIn("<<<NEED\nMy landlord", asked["input"][0]["content"][0]["text"])
        self.assertEqual(agents.finds_left(self.account), 19)
        self.assertFalse(Job.objects.exists(), "free for the user")
        self.tap("ag:card:housing")
        self.tap("ag:back")
        self.assertIn("✨ Found for you", self.last_text())

    def test_a_failed_ai_falls_back_to_words(self):
        self.openai.fail["responses"] = (500, "down")
        self.tap("ag:find")
        self.say("translate")
        self.assertEqual(self.buttons()[0], "ag:card:translator")
        self.assertEqual(agents.finds_left(self.account), 20, "not counted")

    def test_none_left_today(self):
        from apps.core import runtime

        with runtime.override(FIND_PER_DAY=0):
            self.tap("ag:find")
            self.assertIn("You have used today's free finds", self.last_text())
            self.say("Hello")
        self.assertEqual(Job.objects.count(), 1, "typed text is a chat message")


class DemoFindTests(StudioTestCase):
    demo = True

    def test_demo_mode_searches_by_words(self):
        self.tap("ag:find")
        self.say("recipe for dinner")
        self.assertEqual(self.buttons()[0], "ag:card:chef")
        self.assertEqual(self.openai.calls, [])


class LinkTests(StudioTestCase):
    def test_a_shared_link_opens_the_card(self):
        self.say("/start agent_lawyer")
        self.assertIn("🧑‍⚖️ <b>Lawyer</b>", self.tg.last("sendMessage")["text"])
        self.tap("ag:back")
        self.assertIn("🧩 <b>Agents</b>", self.last_text())

    def test_a_link_to_nothing_opens_the_menu(self):
        self.say("/start agent_nobody")
        self.assertIn("Just type to chat", self.last_text())
        self.say("/start ref123")
        self.assertIn("Just type to chat", self.last_text())

    def test_a_new_user_gets_the_card_once_registered(self):
        ControlPanel.objects.update(open_to_everyone=True)
        post_update(message_update("/start agent_chef", user_id=77))
        self.assertIn("share your phone number", self.tg.last("sendMessage")["text"])
        post_update(contact_update("+447700900456", user_id=77))
        self.assertIn("👨‍🍳 <b>Chef</b>", self.tg.last("sendMessage")["text"])
        self.assertTrue(Account.objects.get(telegram_user_id=77).is_registered)

    def test_the_share_link(self):
        chef = agents.get("chef")
        self.assertEqual(agents.share_link(chef), "https://t.me/AIstarsBot?start=agent_chef")


class DraftTests(StudioTestCase):
    def test_the_owner_drafts_agents_with_ai(self):
        from apps.studio import tasks

        self.openai.judge = (
            '{"agents": [{"slug": "Tenant Rights", "emoji": "🏘️", "name": "Tenant rights", '
            '"description": "Your rights as a tenant.", "first_message": "Where do you rent?", '
            '"starters": ["Can I be evicted in winter?", "Who fixes the boiler?"], '
            '"tags": "tenant, eviction", "instructions": "You explain tenant rights."}, '
            '{"slug": "lawyer", "emoji": "x", "name": "Taken", "description": "d", '
            '"first_message": "f", "starters": [], "tags": "t", "instructions": "i"}, '
            '{"slug": "broken"}]}')
        law = Category.objects.get(slug="law")
        self.assertEqual(tasks.draft_agents(law.pk), 1)
        drafted = Assistant.objects.get(slug="tenant-rights")
        self.assertFalse(drafted.active)
        self.assertEqual((drafted.category, drafted.starters),
                         (law, ["Can I be evicted in winter?", "Who fixes the boiler?"]))

    def test_an_answer_that_is_not_json(self):
        from apps.core.models import ActivityLog
        from apps.studio import tasks

        self.openai.judge = "Sure! Here are some agents…"
        self.assertEqual(tasks.draft_agents(Category.objects.get(slug="law").pk), 0)
        self.assertTrue(ActivityLog.objects.filter(event="draft_agents_failed").exists())


class AdminTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User

        self.client.force_login(User.objects.create_superuser("owner", "o@example.com", "pw"))

    def test_the_agents_list_shows_use(self):
        self.tap("ag:start:chef")
        self.say("Pasta?")
        page = self.client.get("/admin/studio/assistant/?q=chef")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn('<td class="field-chats">1</td>', html)
        self.assertIn('<td class="field-credits">1</td>', html)

    def test_the_categories_and_drafting(self):
        page = self.client.get("/admin/studio/category/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Law", page.content.decode())
        law = Category.objects.get(slug="law")
        self.openai.judge = '{"agents": []}'
        response = self.client.post("/admin/studio/category/", {
            "action": "draft_agents", "_selected_action": [law.pk]}, follow=True)
        self.assertIn("Drafting agents for 1", response.content.decode())


class ChatModelTests(StudioTestCase):
    def test_the_users_model_when_the_agent_has_none(self):
        sol = AIModel.objects.get(model_id="gpt-6.1-sol")
        chat.choose_model(self.account, None, sol)
        self.tap("ag:card:chef")
        self.assertIn("Model: <b>GPT-6.1 Sol</b>", self.last_text())
