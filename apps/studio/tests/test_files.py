"""
Photos and files through the webhook: every kind the AI reads goes with the
message, and an album (several photos or files sent as one message) is one
question with one answer.
"""
import base64

from apps.ai.models import AIModel
from apps.core import runtime
from apps.studio import chat, files
from apps.studio.models import AlbumItem, Job, Message, Status

from .base import StudioTestCase

ARCHIVE = -1001234567890
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class FileTests(StudioTestCase):
    def test_a_pdf_goes_with_its_question(self):
        self.send_document("pdf-1", "report.pdf", caption="What does page 2 say?")
        job = Job.objects.get()
        self.assertEqual(job.status, Status.DONE)
        content = self.asked()["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_file")
        self.assertEqual(content[0]["filename"], "report.pdf")
        self.assertTrue(content[0]["file_data"].startswith("data:application/pdf;base64,JVBER"))
        self.assertEqual(content[1], {"type": "input_text", "text": "What does page 2 say?"})
        message = Message.objects.get(role="user")
        self.assertEqual(message.files, [{"file_id": "pdf-1", "name": "report.pdf",
                                          "mime": "application/pdf", "size": 200_000,
                                          "pages": 3}])

    def test_its_pages_set_the_hold(self):
        self.send_document("pdf-1", "report.pdf", caption="Summarize")
        job = Job.objects.get()
        # Three pages at 1,800 tokens each are in the most it could cost.
        self.assertGreaterEqual(job.estimate_micros,
                                job.model.cost_micros(3 * files.PDF_PAGE_TOKENS,
                                                      job.model.max_output_tokens))

    def test_a_file_without_a_question_is_summarized_in_the_users_language(self):
        self.send_document("text-1", "notes.txt", mime="text/plain")
        self.assertEqual(Message.objects.get(role="user").text, "Summarize this file.")
        file = self.asked()["input"][0]["content"][0]
        self.assertEqual(file["file_data"],
                         "data:text/plain;base64," + base64.b64encode(
                             "Notes: ünïcode".encode()).decode())

    def test_office_files_go_as_they_are(self):
        self.send_document("doc-1", "cv.docx", mime=DOCX, caption="Improve it")
        file = self.asked()["input"][0]["content"][0]
        self.assertEqual(file["filename"], "cv.docx")
        self.assertTrue(file["file_data"].startswith(f"data:{DOCX};base64,"))

    def test_code_with_no_mime_type_is_text(self):
        self.send_document("text-2", "main.py", mime="", caption="Review")
        file = self.asked()["input"][0]["content"][0]
        self.assertTrue(file["file_data"].startswith("data:text/plain;base64,"))

    def test_a_file_the_ai_cannot_read(self):
        self.send_document("zip-1", "photos.zip", mime="application/zip", caption="Open it")
        self.assertIn("photos.zip cannot be read. The AI reads photos, PDFs, Word", self.last_text())
        self.assertFalse(Job.objects.exists())

    def test_a_video(self):
        update_text = self.last_text()
        from apps.core.tests.helpers import message_update, post_update

        update = message_update("")
        update["message"].pop("text")
        update["message"]["video"] = {"file_id": "video-1", "duration": 5}
        post_update(update)
        self.assertNotEqual(update_text, self.last_text())
        self.assertIn("Videos cannot be read yet", self.last_text())

    def test_over_twenty_megabytes(self):
        self.send_document("pdf-9", "big.pdf", size=25 * 1024 * 1024, caption="Read")
        self.assertIn("big.pdf is over 20 MB, the most a bot can download", self.last_text())
        self.assertFalse(Job.objects.exists())

    def test_claude_reads_pdfs_and_text_but_not_word(self):
        from apps.ai.models import Provider

        from apps.studio import offer

        Provider.objects.filter(kind="anthropic").update(api_key="sk-ant")
        offer.choose(self.account, Provider.objects.get(kind="anthropic"))
        self.send_document("doc-1", "cv.docx", mime=DOCX, caption="Improve it")
        self.assertIn("Not read with Anthropic: cv.docx. The AI reads photos, PDFs, text and "
                      "code files.", self.last_text())
        self.assertFalse(Job.objects.exists())

    def test_a_model_without_eyes_cannot_read_pdfs(self):
        AIModel.objects.filter(model_id="gpt-6-luna").update(sees_images=False)
        self.send_document("pdf-1", "report.pdf", caption="Read")
        self.assertIn("GPT-6 Luna cannot read report.pdf", self.last_text())

    def test_files_stay_in_the_chat(self):
        self.send_document("pdf-1", "report.pdf", caption="Summarize")
        self.say("And the conclusion?")
        self.assertEqual(self.parts(message=0), ["input_file", "input_text"])
        self.assertEqual(self.said()[-1], ("user", "And the conclusion?"))

    def test_the_newest_files_go_first_when_they_do_not_all_fit(self):
        from unittest import mock

        self.send_document("pdf-1", "old.pdf", caption="One")
        self.send_document("pdf-2", "new.pdf", caption="Two")
        with mock.patch.object(files, "MAX_ITEMS", 1):
            self.say("Three")
        self.assertEqual(self.said()[0], ("user", "[file: old.pdf] One"))
        self.assertEqual(self.parts(message=2), ["input_file", "input_text"])

    def test_more_than_one_message_can_carry(self):
        items = [{"kind": "photo", "file_id": f"photo-{n}"} for n in range(21)]
        chat.send(self.account, 42, "Look", attachments=items)
        self.assertIn("more than 20 files for one message", self.last_text())
        big = [{"kind": "document", "file_id": f"pdf-{n}", "name": f"{n}.pdf",
                "mime": "application/pdf", "size": 19 * 1024 * 1024} for n in range(3)]
        chat.send(self.account, 42, "Read", attachments=big)
        self.assertIn("over 50 MB together", self.last_text())
        self.assertFalse(Job.objects.exists())

    @runtime.override(ARCHIVE_CHAT_ID=ARCHIVE)
    def test_files_are_archived(self):
        self.send_document("pdf-1", "report.pdf", caption="Read")
        job = Job.objects.get()
        archived = [payload for method, payload in self.tg.calls
                    if method == "sendDocument" and payload["chat_id"] == ARCHIVE]
        self.assertEqual([(p["document"], p["caption"]) for p in archived],
                         [("pdf-1", f"#J{job.pk} · user 42 · input")])

    def test_an_image_sent_as_a_file_is_a_photo(self):
        self.send_document("photo-5", "scan.png", mime="image/png", caption="What is it?")
        self.assertEqual(self.parts(message=0), ["input_image", "input_text"])
        self.assertEqual(Message.objects.get(role="user").photos, ["photo-5"])


class AlbumTests(StudioTestCase):
    def test_an_album_is_one_question_with_one_answer(self):
        self.send_album(("photo", "photo-1", ""), ("photo", "photo-2", "Which is nicer?"),
                        ("photo", "photo-3", ""))
        self.assertFalse(Job.objects.exists(), "the album is still coming")
        self.assertEqual(self.flush(), "sent")
        job = Job.objects.get()
        self.assertEqual(job.photos, ["photo-1", "photo-2", "photo-3"])
        self.assertEqual(self.parts(message=0), ["input_image"] * 3 + ["input_text"])
        self.assertEqual(self.said(), [("user", "Which is nicer?")])
        self.assertEqual(Message.objects.filter(role="user").count(), 1)
        self.assertEqual(len(self.openai.sent("moderations")), 3, "one photo a moderation call")
        self.assertFalse(AlbumItem.objects.exists())

    def test_files_in_an_album_without_a_question(self):
        self.send_album(("document", "pdf-1", "a.pdf", ""),
                        ("document", "text-1", "b.txt", "", "text/plain"))
        self.flush()
        self.assertEqual(Message.objects.get(role="user").text, "Summarize these files.")
        self.assertEqual(self.parts(message=0), ["input_file", "input_file", "input_text"])

    def test_it_waits_while_pieces_are_coming_and_goes_once(self):
        from apps.studio import album

        self.send_album(("photo", "photo-1", "Hi"))
        group = f"{self.account.pk}:album-1"
        self.assertEqual(album.flush(self.account.pk, group), "waiting")
        self.assertEqual(self.flush(), "sent")
        self.assertEqual(self.flush(), "gone")
        self.assertEqual(Job.objects.count(), 1)

    def test_pieces_whose_check_never_ran_are_forgotten(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.studio import tasks

        self.send_album(("photo", "photo-1", "Hi"))
        AlbumItem.objects.update(created_at=timezone.now() - timedelta(hours=2))
        tasks.sweep_jobs()
        self.assertFalse(AlbumItem.objects.exists())

    def test_one_file_the_ai_cannot_read_stops_the_album(self):
        self.send_album(("document", "pdf-1", "a.pdf", "Compare"),
                        ("document", "zip-1", "b.zip", "", "application/zip"))
        self.flush()
        self.assertIn("b.zip cannot be read", self.last_text())
        self.assertFalse(Job.objects.exists())

    def test_photos_for_edit_a_photo(self):
        self.tap("img:")
        self.tap("img:edit")
        self.send_album(("photo", "photo-1", "Put the cup on the table"),
                        ("photo", "photo-2", ""))
        self.flush()
        self.assertIn("Your photo edit · 🖼 2", self.last_text())
        self.tap("img:go")
        job = Job.objects.get()
        self.assertEqual((job.kind, job.photos), ("edit", ["photo-1", "photo-2"]))
        sent = self.openai.sent("images/edits")[0]["files"]
        self.assertEqual([field for field, _ in sent], ["image[]", "image[]"])


class VoiceAlbumTests(StudioTestCase):
    def test_several_audio_files_are_heard_together(self):
        from apps.core.tests.helpers import message_update, post_update

        for number in (1, 2):
            update = message_update("")
            update["message"].pop("text")
            update["message"]["audio"] = {"file_id": f"audio-{number}", "duration": 30,
                                          "file_name": f"part{number}.mp3"}
            update["message"]["media_group_id"] = "album-1"
            post_update(update)
        self.flush()
        heard = Job.objects.get(kind="transcribe")
        # A minute together at $0.0045 a minute.
        self.assertEqual((heard.photos, heard.cost_micros), (["audio-1", "audio-2"], 4500))
        self.assertEqual(len(self.openai.sent("audio/transcriptions")), 2)
        answer = Job.objects.get(kind="chat")
        self.assertEqual(answer.prompt, "What is the weather like on Mars?\n\n"
                                        "What is the weather like on Mars?")
