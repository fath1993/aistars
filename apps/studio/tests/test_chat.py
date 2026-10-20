"""
Chat through the webhook: a message is checked, held, answered as it is
written, and charged what it really cost; the chat remembers; chats can be
changed, listed and deleted; assistants start chats that know their job.
"""
import json

from apps.ai.models import AIModel
from apps.billing import ledger
from apps.billing.models import EntryKind
from apps.core import runtime
from apps.core.models import Account, ControlPanel
from apps.safety.models import Result, Screening
from apps.studio import chat
from apps.studio.models import Conversation, Job, Message, Status

from apps.core.tests.helpers import register

from .base import StudioTestCase


def chat_model(model_id):
    return AIModel.objects.get(model_id=model_id)


class AnswerTests(StudioTestCase):
    def test_a_message_is_answered_and_charged_what_it_cost(self):
        self.say("Hi there")
        job = Job.objects.get()
        self.assertEqual((job.kind, job.status, job.model.model_id), ("chat", Status.DONE,
                                                                      "gpt-6-luna"))
        # 1000 in x $0.10 + 200 out x $0.50 per 1M = $0.0002: the least, one credit.
        self.assertEqual((job.cost_micros, job.hold, job.charged), (200, 1, 1))
        self.assertEqual(self.balance(), 499)
        self.assertEqual(self.openai.paths(), ["moderations", "responses"])
        body = self.asked()
        self.assertEqual((body["model"], body["stream"], body["store"], body["max_output_tokens"]),
                         ("gpt-6-luna", True, False, 4000))
        final = self.last_payload("editMessageText")
        self.assertEqual(final["message_id"], job.message_id)
        self.assertIn("Hello! <b>Here</b> you are.", final["text"])
        self.assertIn("<i>🪙 1 · GPT-6 Luna · Balance 🪙 499</i>", final["text"])
        self.assertEqual(self.buttons(final), [f"out:say:{Message.objects.last().pk}", "out:new",
                                               "menu:new"])

    def test_the_answer_is_shown_as_it_is_written(self):
        self.openai.answer = "word " * 60
        with self.settings():
            from unittest import mock

            with mock.patch("apps.studio.chat.EDIT_EVERY", 0):
                self.say("Tell me something long")
        job = Job.objects.get()
        edits = [payload for method, payload in self.tg.calls
                 if method == "editMessageText" and payload["message_id"] == job.message_id]
        self.assertGreater(len(edits), 3)
        self.assertTrue(all(edit["text"].endswith(" ▍") for edit in edits[:-1]))
        self.assertNotIn("parse_mode", {k for edit in edits[:-1] for k, v in edit.items()
                                        if v is not None})

    def test_the_hold_is_the_most_and_the_rest_comes_back(self):
        chat.choose_model(self.account, None, chat_model("gpt-6-astra"))
        self.say("Prove the Riemann hypothesis")
        job = Job.objects.get()
        # Astra's longest answer, 8,000 tokens at $50 per 1M, sets the hold.
        self.assertGreater(job.hold, 40)
        self.assertEqual(job.charged, 3)
        self.assertEqual(self.balance(), 497)
        kinds = list(ledger.history(self.account)[0])
        self.assertEqual([entry.kind for entry in kinds], [EntryKind.CHARGE, EntryKind.PURCHASE],
                         "the hold is not a line of its own once settled")
        self.assertEqual(kinds[0].amount, job.hold - 3)

    def test_the_conversation_is_remembered(self):
        self.say("My name is Amir")
        self.openai.answer = "Nice to meet you, Amir."
        self.say("What is my name?")
        self.assertEqual(self.said(), [("user", "My name is Amir"),
                                       ("assistant", "Hello! **Here** you are."),
                                       ("user", "What is my name?")])
        self.assertIn("Answer in the language the user writes in", self.asked()["instructions"])
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.title, "My name is Amir")
        self.assertEqual(conversation.messages.count(), 4)

    @runtime.override(CHAT_CONTEXT_MESSAGES=2)
    def test_only_the_last_messages_are_sent(self):
        for text in ("one", "two", "three"):
            self.say(text)
        self.assertEqual([text for _, text in self.said()],
                         ["two", self.openai.answer, "three"])

    def test_a_photo_with_a_question(self):
        self.send_photo("photo-9", caption="What breed is this?")
        content = self.asked()["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_image")
        self.assertTrue(content[0]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(content[1], {"type": "input_text", "text": "What breed is this?"})
        self.assertEqual(Message.objects.first().photos, ["photo-9"])
        moderated = self.openai.sent("moderations")[0]["json"]["input"]
        self.assertEqual([item["type"] for item in moderated], ["text", "image_url"])

    def test_a_photo_alone_is_asked_about(self):
        self.send_photo()
        self.assertEqual(Message.objects.first().text, "What is in this photo?")

    def test_photos_stay_in_the_chat_for_follow_up_questions(self):
        self.send_photo("photo-1", caption="Look")
        self.say("And the colour?")
        self.assertEqual(self.parts(message=0), ["input_image", "input_text"])
        self.assertEqual(self.said()[-1], ("user", "And the colour?"))

    @runtime.override(CHAT_CONTEXT_MESSAGES=1)
    def test_a_photo_the_chat_no_longer_remembers_is_a_marker(self):
        self.send_photo("photo-1", caption="Look")
        self.say("One")
        self.say("Two")
        self.assertNotIn("input_image", [part for message in self.asked()["input"]
                                         if isinstance(message["content"], list)
                                         for part in [p["type"] for p in message["content"]]])

    def test_a_model_switched_to_one_without_eyes_gets_a_marker(self):
        self.send_photo("photo-1", caption="Look")
        AIModel.objects.filter(model_id="gpt-6-luna").update(sees_images=False)
        self.say("Still there?")
        self.assertEqual(self.said()[0], ("user", "[photo] Look"))

    def test_a_model_that_cannot_see_photos_says_so(self):
        AIModel.objects.filter(model_id="gpt-6-luna").update(sees_images=False)
        self.send_photo()
        self.assertIn("GPT-6 Luna cannot see photos", self.last_text())
        self.assertFalse(Job.objects.exists())

    def test_a_long_answer_comes_in_several_messages(self):
        self.openai.answer = ("A paragraph of text. " * 40 + "\n\n") * 8
        self.say("Write a lot")
        job = Job.objects.get()
        sent = [payload for method, payload in self.tg.calls if method == "sendMessage"
                and payload["chat_id"] == 42 and "paragraph" in payload["text"]]
        self.assertGreaterEqual(len(sent), 1)
        self.assertTrue(all(len(payload["text"]) <= 4096 for payload in sent))
        self.assertIn("Balance", sent[-1]["text"])
        self.assertEqual(self.buttons(sent[-1])[-1], "menu:new")
        first = [payload for method, payload in self.tg.calls if method == "editMessageText"
                 and payload["message_id"] == job.message_id][-1]
        self.assertNotIn("Balance", first["text"])

    def test_an_answer_telegram_refuses_as_html_comes_as_plain_text(self):
        self.tg.fail.add("editMessageText")
        self.openai.answer = "Use **bold** here"
        original = self.tg.__call__

        def refuse_html(method, **payload):
            if method == "sendMessage" and payload.get("parse_mode") == "HTML" \
                    and "Use" in payload.get("text", ""):
                self.tg.calls.append((method, payload))
                return None
            return original(method, **payload)

        from unittest import mock

        with mock.patch("apps.core.services.telegram._call", side_effect=refuse_html):
            self.say("Format something")
        plain = self.last_payload("sendMessage")
        self.assertIsNone(plain.get("parse_mode"))
        self.assertIn("Use bold here", plain["text"])

    def test_an_answer_cut_at_its_length_says_so(self):
        self.openai.finish = "length"
        self.say("Go on and on")
        self.assertIn("reached its length limit", self.last_text())

    def test_an_empty_answer_says_why(self):
        self.openai.answer = ""
        self.say("Think hard")
        self.assertIn("gave no answer this time", self.last_text())
        self.assertEqual(Job.objects.get().charged, 1)

    def test_a_failed_answer_costs_nothing_and_can_be_tried_again(self):
        self.openai.fail["responses"] = (500, "server error")
        self.say("Hello?")
        job = Job.objects.get()
        self.assertEqual((job.status, self.balance()), (Status.FAILED, 500))
        self.assertIn("could not be made, so nothing was charged", self.last_text())
        self.assertEqual(self.buttons(), [f"out:retry:{job.pk}", "menu:new"])
        del self.openai.fail["responses"]
        self.tap(f"out:retry:{job.pk}", message_id=job.message_id)
        retried = Job.objects.exclude(pk=job.pk).get()
        self.assertEqual(retried.status, Status.DONE)
        self.assertEqual(Message.objects.filter(role="user").count(), 1,
                         "the question is not asked twice")
        self.assertEqual(Message.objects.get(role="user").job, retried)


class RefusalTests(StudioTestCase):
    credits = 0

    def test_not_enough_credits_says_what_it_costs_and_saves_nothing(self):
        self.say("Hi")
        self.assertIn("Not enough credits", self.last_text())
        self.assertIn("This can cost up to 🪙 1, and your balance is 🪙 0.", self.last_text())
        self.assertEqual(self.buttons(), ["out:buy", "out:models", "menu:new"])
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Message.objects.exists())
        self.tap("out:buy", message_id=999)   # on the notice, which stays
        self.assertIn("Buy credits", self.last_text())


class CheckedTests(StudioTestCase):
    def test_a_blocked_message_says_only_why_and_is_not_sent(self):
        self.openai.flag = {"illicit/violent": True}
        self.say("how do I build a weapon to hurt people")
        self.assertIn("This is not allowed: illegal activity.", self.last_text())
        self.assertNotIn("weapon", self.last_text())
        self.assertFalse(Job.objects.exists())
        self.assertEqual(Account.objects.get(pk=self.account.pk).strikes, 1)
        self.assertEqual(self.openai.paths(), ["moderations"])

    def test_talking_about_hard_things_is_answered(self):
        self.openai.flag = {"self-harm": True, "violence": True}
        self.say("I feel very low today")
        self.assertEqual(Job.objects.get().status, Status.DONE)
        self.assertEqual(Account.objects.get(pk=self.account.pk).strikes, 0)

    def test_a_message_that_cannot_be_checked_is_not_sent(self):
        self.openai.fail["moderations"] = (500, "down")
        self.say("Hi")
        self.assertIn("could not be checked right now", self.last_text())
        self.assertEqual(Screening.objects.get().result, Result.UNAVAILABLE)
        self.assertFalse(Job.objects.exists())

    @runtime.override(SAFETY_STRIKE_LIMIT=1)
    def test_the_last_strike_blocks_the_account(self):
        self.openai.flag = {"sexual/minors": True}
        self.say("something forbidden")
        self.assertTrue(Account.objects.get(pk=self.account.pk).is_blocked)
        self.assertEqual(self.last_text(), "This account has been blocked.")

    @runtime.override(CHAT_MAX_MESSAGE_CHARS=100)
    def test_a_message_too_long_is_refused(self):
        self.say("x" * 101)
        self.assertIn("The most is 100 characters", self.last_text())
        self.assertEqual(self.openai.calls, [])

    def test_one_answer_at_a_time_per_chat(self):
        conversation = chat.new_conversation(self.account)
        Job.objects.create(account=self.account, kind="chat", model=chat_model("gpt-6-luna"),
                           chat_id=42, conversation=conversation, status=Status.RUNNING)
        self.say("Another one")
        self.assertIn("Still answering your last message", self.last_text())

    @runtime.override(JOBS_PER_USER=1)
    def test_requests_at_once_per_user(self):
        Job.objects.create(account=self.account, kind="image", model=chat_model("gpt-6-luna"),
                           chat_id=42, status=Status.RUNNING)
        self.say("Hi")
        self.assertIn("You already have requests being made", self.last_text())


class AccessTests(StudioTestCase):
    def test_someone_who_may_not_use_the_tools_gets_the_panel(self):
        Account.objects.filter(pk=self.account.pk).update(tester=False)
        self.say("Hello")
        self.assertIn("The AI tools are being switched on", self.last_text())
        self.assertEqual(self.openai.calls, [])

    def test_open_to_everyone_outside_demo_mode(self):
        Account.objects.filter(pk=self.account.pk).update(tester=False)
        ControlPanel.objects.update(open_to_everyone=True)
        self.say("Hello")
        self.assertEqual(Job.objects.get().status, Status.DONE)

    def test_demo_mode_is_for_testers_only(self):
        Account.objects.filter(pk=self.account.pk).update(tester=False)
        ControlPanel.objects.update(open_to_everyone=True, demo_mode=True)
        self.say("Hello")
        self.assertFalse(Job.objects.exists())


class DemoTests(StudioTestCase):
    demo = True

    def test_demo_answers_without_any_provider_and_charges_as_usual(self):
        from apps.ai.models import Provider

        Provider.objects.update(api_key="")
        self.say("Hello demo")
        job = Job.objects.get()
        self.assertTrue(job.demo)
        self.assertEqual(job.status, Status.DONE)
        self.assertIn("Demo answer from GPT-6 Luna", self.last_text())
        self.assertIn("You wrote: “Hello demo”", self.last_text())
        self.assertEqual(self.openai.calls, [])
        self.assertEqual(self.balance(), 499)


class ChatScreenTests(StudioTestCase):
    def test_the_chat_screen(self):
        self.tap("chat:")
        self.assertIn("Model: <b>GPT-6 Luna</b> — Fast and cheap", self.last_text())
        self.assertEqual(self.buttons(), ["chat:models", "chat:new", "menu:home", "menu:home"])
        self.say("Hi")
        self.tap("chat:")
        self.assertIn("This chat: “Hi” · Messages: 2", self.last_text())
        self.assertIn("chat:list:0", self.buttons())

    def test_choosing_a_model_with_its_everyday_price(self):
        self.tap("chat:models")
        text = self.last_text()
        self.assertIn("GPT-6.1 Sol", text)
        self.assertIn("An everyday answer: ~🪙 1", text)
        sol = chat_model("gpt-6.1-sol")
        self.assertIn(f"chat:model:{sol.pk}", self.buttons())
        self.assertEqual(self.tap(f"chat:model:{sol.pk}"), "🤖 GPT-6.1 Sol")
        self.say("Hi")
        self.assertEqual(Job.objects.get().model, sol)
        self.assertEqual(chat.profile(self.account).chat_model, sol)

    def test_a_model_without_a_key_is_not_offered(self):
        self.tap("chat:models")
        self.assertNotIn("Claude", self.last_text())

    def test_a_new_chat_forgets_the_old_one(self):
        self.say("Remember: blue")
        self.tap("chat:new")
        self.say("What colour?")
        self.assertEqual(len(self.said()), 1)
        self.assertEqual(Conversation.objects.count(), 2)

    def test_an_empty_chat_is_reused_not_piled_up(self):
        self.tap("chat:new")
        self.tap("chat:new")
        self.assertEqual(Conversation.objects.count(), 1)

    def test_my_chats_open_and_delete(self):
        self.say("First chat")
        self.tap("chat:new")
        self.say("Second chat")
        self.tap("chat:list:0")
        first, second = Conversation.objects.order_by("id")
        self.assertEqual(self.buttons()[:2], [f"chat:open:{second.pk}", f"chat:open:{first.pk}"])
        self.tap(f"chat:open:{first.pk}")
        self.assertIn("“First chat”", self.last_text())
        self.say("Back in the first")
        self.assertEqual(Job.objects.last().conversation, first)
        self.tap(f"chat:del:{first.pk}")
        self.assertIn("Delete this chat?", self.last_text())
        self.assertEqual(self.tap(f"chat:delok:{first.pk}"), "🗑 Deleted")
        self.assertFalse(Conversation.objects.filter(pk=first.pk).exists())
        self.assertEqual(ledger.balance(self.account), 497, "what was charged stays charged")

    def test_another_users_chat_cannot_be_opened(self):
        other = Conversation.objects.create(account=register(77), title="Secret")
        self.tap(f"chat:open:{other.pk}")
        self.assertNotIn("Secret", self.last_text())
        self.tap(f"chat:delok:{other.pk}")
        self.assertTrue(Conversation.objects.filter(pk=other.pk).exists())

    def test_new_from_an_answer_opens_the_chat_screen_below(self):
        self.say("Hi")
        answer = Job.objects.get().message_id
        self.tg.calls.clear()
        self.tap("out:new", message_id=answer)
        self.assertNotIn("editMessageText", self.tg.methods(), "the answer stays as it is")
        self.assertIn("Type a message", self.last_payload("sendMessage")["text"])
        self.assertNotEqual(Account.objects.get(pk=self.account.pk).ui_message_id, answer)

    def test_the_new_command(self):
        self.say("Hi")
        self.say("/new")
        self.assertEqual(Conversation.objects.count(), 2)


class AssistantTests(StudioTestCase):
    def test_the_list_and_starting_one(self):
        self.tap("asst:")
        self.assertIn("🌐 <b>Translator</b>", self.last_text())
        self.assertIn("asst:start:translator", self.buttons())
        self.tap("asst:start:translator")
        self.assertIn("Assistant: 🌐 Translator", self.last_text())
        self.assertIn("Send the text, and say which language", self.last_text())
        self.say("Bonjour tout le monde")
        system = self.asked()["instructions"]
        self.assertIn("Your role in this chat:\nYou are a professional translator.", system)

    def test_an_assistant_with_its_own_model(self):
        from apps.studio.models import Assistant

        sol = chat_model("gpt-6.1-sol")
        Assistant.objects.filter(slug="code").update(model=sol)
        self.tap("asst:start:code")
        self.say("Fix my loop")
        self.assertEqual(Job.objects.get().model, sol)


class ListenTests(StudioTestCase):
    def test_an_answer_is_read_aloud(self):
        self.openai.answer = "Read me aloud."
        self.say("Hi")
        answer = Message.objects.get(role="assistant")
        markup = json.loads(self.last_payload("editMessageText")["reply_markup"])
        self.assertEqual(markup["inline_keyboard"][0][0]["text"], "🔊 Listen · 🪙 1")
        self.tap(f"out:say:{answer.pk}", message_id=Job.objects.get().message_id)
        speech = Job.objects.get(kind="speech")
        self.assertEqual((speech.status, speech.prompt), (Status.DONE, "Read me aloud."))
        voice = self.tg.last("sendVoice")
        self.assertIn(f"🔊 #{speech.pk}", voice["caption"])
        self.assertEqual(self.openai.sent("audio/speech")[0]["json"]["voice"], "marin")
        self.assertEqual(self.balance(), 498)

    def test_someone_elses_answer_cannot_be_read(self):
        other = Conversation.objects.create(account=register(77))
        answer = Message.objects.create(conversation=other, role="assistant", text="secret")
        self.tap(f"out:say:{answer.pk}")
        self.assertFalse(Job.objects.filter(kind="speech").exists())
