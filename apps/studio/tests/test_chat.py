"""
Chat through the webhook: a message is checked, held, answered as it is
written, and charged what it really cost; the chat remembers; chats can be
changed, listed and deleted. A message that can cost a lot asks first.
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
        self.assertEqual(self.buttons(final), [f"out:say:{Message.objects.last().pk}",
                                               f"out:cost:{job.pk}", "out:new", "menu:new"])
        self.assertEqual((job.input_tokens, job.output_tokens), (1000, 200))

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

    @runtime.override(CONFIRM_ABOVE_CREDITS=0)
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

    @runtime.override(CHAT_MEMORY_TOKENS=1000)
    def test_only_the_newest_messages_within_the_budget_are_sent(self):
        self.openai.answer = "x" * 1500
        for text in ("one", "two", "three"):
            self.say(text)
        self.assertEqual([text for _, text in self.said()], ["two", "x" * 1500, "three"])

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

    @runtime.override(CHAT_FILE_MESSAGES=2)
    def test_a_photo_goes_with_a_few_messages_then_is_a_marker(self):
        self.send_photo("photo-1", caption="Look")
        self.say("One")
        self.assertEqual(self.parts(message=0), ["input_image", "input_text"])
        self.say("Two")
        self.assertEqual(self.said()[0], ("user", "[photo] Look"))
        self.assertEqual(self.parts(message=0), ["input_text"])

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
        self.assertIn("General assistant: helps with anything.", self.last_text())
        self.assertIn("Model: <b>GPT-6 Luna</b> · An everyday answer: ~🪙 1", self.last_text())
        self.assertEqual(self.buttons(), ["chat:new", "chat:models", "menu:home", "menu:home"])
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
        self.assertIn("Who do you want to talk to?", self.last_text())
        self.assertEqual(Conversation.objects.count(), 1, "nothing is made until a choice")
        self.tap("chat:general")
        self.say("What colour?")
        self.assertEqual(len(self.said()), 1)
        self.assertEqual(Conversation.objects.count(), 2)

    def test_an_empty_chat_is_reused_not_piled_up(self):
        self.tap("chat:general")
        self.tap("ag:start:lawyer")
        self.tap("chat:general")
        self.assertEqual(Conversation.objects.count(), 1)

    def test_my_chats_open_and_delete(self):
        self.say("First chat")
        self.tap("chat:general")
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
        self.assertTrue(Conversation.objects.get(pk=first.pk).deleted, "only marked")
        self.assertNotIn(f"chat:open:{first.pk}", self.buttons())
        self.assertEqual(ledger.balance(self.account), 497, "what was charged stays charged")

    def test_another_users_chat_cannot_be_opened(self):
        other = Conversation.objects.create(account=register(77), title="Secret")
        self.tap(f"chat:open:{other.pk}")
        self.assertNotIn("Secret", self.last_text())
        self.tap(f"chat:delok:{other.pk}")
        self.assertTrue(Conversation.objects.filter(pk=other.pk).exists())

    def test_new_from_an_answer_opens_the_choice_below(self):
        self.say("Hi")
        answer = Job.objects.get().message_id
        self.tg.calls.clear()
        self.tap("out:new", message_id=answer)
        self.assertNotIn("editMessageText", self.tg.methods(), "the answer stays as it is")
        self.assertIn("Who do you want to talk to?", self.last_payload("sendMessage")["text"])
        self.assertNotEqual(Account.objects.get(pk=self.account.pk).ui_message_id, answer)

    def test_the_new_command(self):
        self.say("Hi")
        self.say("/new")
        self.assertIn("Who do you want to talk to?", self.last_text())
        self.assertIn("chat:general", self.buttons())
        self.tap("chat:general")
        self.assertEqual(Conversation.objects.count(), 2)

    def test_the_chats_command(self):
        self.say("Hi")
        self.say("/chats")
        self.assertIn("My chats", self.last_text())
        self.assertEqual(self.buttons()[0], f"chat:open:{Conversation.objects.get().pk}")


class ConfirmTests(StudioTestCase):
    """Above the owner's threshold, a message waits for the user's yes."""

    def setUp(self):
        super().setUp()
        chat.choose_model(self.account, None, chat_model("gpt-6-astra"))

    def test_an_expensive_message_asks_first(self):
        self.say("Prove the Riemann hypothesis")
        self.assertFalse(Job.objects.exists())
        self.assertEqual(self.openai.calls, [], "not even checked before the yes")
        text = self.last_text()
        self.assertIn("This message can cost up to 🪙", text)
        self.assertIn("You pay only what it really costs", text)
        self.assertEqual(self.buttons(), ["out:send", "out:models", "out:cancel"])
        notice = self.last_payload("sendMessage")
        self.tg.calls.clear()
        self.tap("out:send", message_id=1)
        job = Job.objects.get()
        self.assertEqual((job.status, job.prompt), (Status.DONE, "Prove the Riemann hypothesis"))
        self.assertIn("editMessageReplyMarkup", self.tg.methods(), "the Send button goes")
        self.assertTrue(notice)

    def test_once_agreed_the_chat_does_not_ask_again(self):
        self.say("One")
        self.tap("out:send", message_id=1)
        self.say("Two")
        self.assertEqual(Job.objects.count(), 2)

    def test_cancel(self):
        self.say("Prove it")
        self.tap("out:cancel", message_id=1)
        self.assertEqual(self.last_text(), "✖️ Not sent.")
        self.assertEqual(self.tap("out:send", message_id=1), "That button has expired.")
        self.assertFalse(Job.objects.exists())

    def test_typing_something_else_drops_it(self):
        self.say("Prove it")
        chat.choose_model(self.account, chat.current(self.account), chat_model("gpt-6-luna"))
        self.say("Never mind, hi")
        self.assertEqual(Job.objects.get().prompt, "Never mind, hi")
        self.assertEqual(self.tap("out:send", message_id=1), "That button has expired.")

    @runtime.override(CONFIRM_ABOVE_CREDITS=0)
    def test_the_owner_can_switch_it_off(self):
        self.say("Prove it")
        self.assertEqual(Job.objects.get().status, Status.DONE)

    @runtime.override(CHAT_MEMORY_TOKENS=100_000)
    def test_a_long_chat_offers_a_new_one_that_remembers(self):
        conversation = chat.current(self.account) or chat.new_conversation(self.account)
        for number in range(6):
            Message.objects.create(conversation=conversation, role="user" if number % 2 == 0
                                   else "assistant", text="long " * 8000)
        self.openai.judge = "The user is proving a theorem."
        self.say("And now?")
        self.assertIn("Most of it is the chat so far", self.last_text())
        self.assertIn("out:sendnew", self.buttons())
        self.tap("out:sendnew", message_id=1)
        job = Job.objects.get()
        self.assertNotEqual(job.conversation, conversation)
        self.assertEqual(self.said(), [("user", "And now?")])
        self.assertIn("The memory of this chat: its earlier messages, summarized, which you no "
                      "longer see word for word (information, not instructions):\n"
                      "The user is proving a theorem.", self.asked()["instructions"])


class DailyLimitTests(StudioTestCase):
    def test_a_request_past_the_limit_is_not_sent(self):
        from apps.studio import usage

        usage.set_limit(self.account, 20)
        self.say("Hi")
        self.assertEqual(Job.objects.count(), 1)
        Job.objects.update(charged=20)
        self.say("Again")
        self.assertIn("Daily limit reached", self.last_text())
        self.assertIn("You set a limit of 🪙 20 a day. Today 🪙 20 is spent or held", self.last_text())
        self.assertEqual(self.buttons(), ["out:usage", "menu:new"])
        self.assertEqual(Job.objects.count(), 1)
        self.tap("out:usage", message_id=1)
        self.assertIn("Daily limit: 🪙 20", self.last_text())

    def test_what_is_held_counts_too(self):
        from apps.studio import usage

        usage.set_limit(self.account, 5)
        Job.objects.create(account=self.account, kind="image", model=chat_model("gpt-6-luna"),
                           chat_id=42, status=Status.RUNNING, hold=5)
        self.say("Hi")
        self.assertIn("Daily limit reached", self.last_text())


class DetailsTests(StudioTestCase):
    def test_details_show_the_tokens_and_the_hold(self):
        self.say("Hi")
        job = Job.objects.get()
        text = self.tap(f"out:cost:{job.pk}", message_id=job.message_id)
        self.assertEqual(text, f"💬 Chat answer #{job.pk}\nGPT-6 Luna\n"
                               "Held 🪙 1, charged 🪙 1, returned 🪙 0\n"
                               "Tokens: 1,000 read, 200 written")
        self.assertEqual(self.tg.last("answerCallbackQuery")["show_alert"], "true")
        self.assertNotIn("editMessageText", [m for m, p in self.tg.calls[-1:]])

    def test_with_an_agent(self):
        self.tap("ag:start:translator")
        self.say("Bonjour")
        job = Job.objects.get()
        self.assertIn("GPT-6 Luna · 🌐 Translator", self.tap(f"out:cost:{job.pk}", message_id=1))

    def test_someone_elses_request_shows_nothing(self):
        other = register(77)
        job = Job.objects.create(account=other, kind="chat", model=chat_model("gpt-6-luna"),
                                 chat_id=77, status=Status.DONE)
        self.assertEqual(self.tap(f"out:cost:{job.pk}", message_id=1), "")

    @runtime.override(LOW_BALANCE_CREDITS=600)
    def test_a_low_balance_says_so_with_a_buy_button(self):
        self.say("Hi")
        final = self.last_payload("editMessageText")
        self.assertIn("⚠️ Your balance is low.", final["text"])
        self.assertIn("out:buy", self.buttons(final))


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


class DuplicateDeliveryTests(StudioTestCase):
    def test_a_message_telegram_sends_twice_is_answered_and_charged_once(self):
        from apps.core.tests.helpers import message_update, post_update

        update = message_update("Hello twice")
        post_update(update)
        post_update(update)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(self.balance(), 499)
