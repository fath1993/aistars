"""
Voice through the webhook: a voice note in a chat becomes text the AI
answers; Voice to text gives the text back; Text to speech reads a text
aloud. Both are priced by their size before they start.
"""
from apps.core import runtime
from apps.studio.models import Job, Message, Status

from .base import StudioTestCase

ARCHIVE = -1001234567890


class VoiceChatTests(StudioTestCase):
    def test_a_voice_note_is_heard_then_answered(self):
        self.send_voice("voice-1", seconds=40)
        heard, answer = Job.objects.order_by("id")
        self.assertEqual((heard.kind, heard.status), ("transcribe", Status.DONE))
        # 40 seconds at $0.0045 a minute: one credit, priced before it started.
        self.assertEqual((heard.cost_micros, heard.hold, heard.charged), (3000, 1, 1))
        self.assertEqual((answer.kind, answer.status, answer.prompt),
                         ("chat", Status.DONE, "What is the weather like on Mars?"))
        self.assertIn("🎙 <i>What is the weather like on Mars?</i>",
                      [text for text in self.messages() if text.startswith("🎙")][-1])
        question = Message.objects.get(role="user")
        self.assertTrue(question.voice)
        sent = self.openai.sent("audio/transcriptions")[0]
        self.assertEqual(sent["files"]["file"][0], "voice.ogg")
        self.assertEqual(sent["data"]["model"], "gpt-transcribe")
        self.assertEqual(self.balance(), 498)

    def test_nothing_heard_is_not_sent_to_the_chat(self):
        self.openai.transcript = ""
        self.send_voice()
        self.assertEqual(list(Job.objects.values_list("kind", flat=True)), ["transcribe"])
        self.assertIn("No words could be heard", self.last_text())

    @runtime.override(ARCHIVE_CHAT_ID=ARCHIVE)
    def test_the_voice_note_is_archived(self):
        self.send_voice("voice-5")
        heard = Job.objects.get(kind="transcribe")
        archived = [payload for method, payload in self.tg.calls if method == "sendVoice"
                    and payload["chat_id"] == ARCHIVE][0]
        self.assertEqual((archived["voice"], archived["caption"]),
                         ("voice-5", f"#J{heard.pk} · user 42 · input"))

    def test_too_long_or_without_a_length(self):
        self.send_voice(seconds=21 * 60)
        self.assertIn("up to 20 minutes", self.last_text())
        self.send_voice(seconds=0)
        self.assertIn("so its length is known", self.last_text())
        self.assertFalse(Job.objects.exists())

    def test_a_failure_returns_the_credits(self):
        self.openai.fail["audio/transcriptions"] = (500, "down")
        self.send_voice()
        self.assertEqual((Job.objects.get().status, self.balance()), (Status.FAILED, 500))
        self.assertIn("could not be done, so nothing was charged", self.last_text())


class VoiceToTextTests(StudioTestCase):
    def test_the_text_comes_back_and_nothing_is_answered(self):
        self.tap("voice:")
        self.assertEqual(self.buttons(), ["voice:stt", "voice:tts", "voice:voices", "menu:home",
                                          "menu:home"])
        self.tap("voice:stt")
        self.send_voice("audio-1", seconds=90, kind="audio")
        job = Job.objects.get()
        self.assertEqual((job.kind, job.status, job.options["then_chat"]),
                         ("transcribe", Status.DONE, False))
        self.assertIn("📝 What is the weather like on Mars?", self.last_text())
        self.assertIn("Balance", self.last_text())
        self.assertEqual(self.openai.sent("audio/transcriptions")[0]["files"]["file"][0],
                         "audio.mp3")

    def test_a_long_transcript_comes_in_parts(self):
        self.openai.transcript = "word " * 2000
        self.tap("voice:stt")
        self.send_voice(seconds=600)
        parts = [text for text in self.messages() if "word word" in text]
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 4096 for part in parts))


class TextToSpeechTests(StudioTestCase):
    def test_a_text_is_priced_then_read(self):
        self.tap("voice:tts")
        self.say("Good morning, everyone!")
        text = self.last_text()
        self.assertIn("Characters: 23 · Voice: Marin", text)
        self.assertIn("Price: <b>💎 1</b>", text)
        self.tap("voice:go")
        job = Job.objects.get()
        self.assertEqual((job.kind, job.status, job.prompt),
                         ("speech", Status.DONE, "Good morning, everyone!"))
        voice = self.tg.last("sendVoice")
        self.assertIn(f"🔊 #{job.pk}", voice["caption"])
        self.assertEqual(self.balance(), 499)
        self.assertEqual(self.openai.paths(), ["moderations", "audio/speech"])

    def test_choosing_the_voice(self):
        self.tap("voice:voices")
        self.assertIn("voice:set:nova", self.buttons())
        self.assertEqual(self.tap("voice:set:nova"), "🗣 Nova")
        self.tap("voice:tts")
        self.say("Hi")
        self.assertIn("Voice: Nova", self.last_text())
        self.tap("voice:go")
        self.assertEqual(self.openai.sent("audio/speech")[0]["json"]["voice"], "nova")

    def test_the_voice_can_be_changed_on_the_summary(self):
        self.tap("voice:tts")
        self.say("Hi")
        self.tap("voice:voices")
        self.tap("voice:set:onyx")
        self.assertIn("Voice: Onyx", self.last_text())
        self.assertIn("voice:go", self.buttons())

    def test_a_text_is_checked_like_a_chat_message(self):
        self.openai.flag = {"hate/threatening": True}
        self.tap("voice:tts")
        self.say("something hateful")
        self.tap("voice:go")
        self.assertIn("This is not allowed", self.last_text())
        self.assertFalse(Job.objects.exists())

    @runtime.override(ARCHIVE_CHAT_ID=ARCHIVE)
    def test_the_voice_note_made_is_archived_then_sent(self):
        self.tap("voice:tts")
        self.say("Hello")
        self.tap("voice:go")
        job = Job.objects.get()
        sends = [(payload["chat_id"], payload.get("voice") or "upload") for method, payload
                 in self.tg.calls if method == "sendVoice"]
        self.assertEqual(sends[0], (ARCHIVE, "upload"))
        self.assertEqual(sends[1][0], 42)
        self.assertEqual(Job.objects.get(pk=job.pk).result_file_id, sends[1][1])
