"""
Calling the providers: what is sent, how the answer is read, what it costs,
and that the spend guard is respected.
"""
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.ai import clients
from apps.ai.models import AIModel, Provider
from apps.core.models import ActivityLog, ControlPanel, SpendLedger
from apps.core.services import budget


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


ANTHROPIC_OK = {"content": [{"type": "text", "text": "Bonjour"}],
                "usage": {"input_tokens": 1000, "output_tokens": 200}}
OPENAI_OK = {"status": "completed", "output": [
    {"type": "reasoning", "summary": []},
    {"type": "message", "role": "assistant",
     "content": [{"type": "output_text", "text": "Bonjour"}]}],
    "usage": {"input_tokens": 1000, "output_tokens": 200}}


class ClientTests(TestCase):
    def setUp(self):
        Provider.objects.update(api_key="sk-test")
        self.haiku = AIModel.objects.get(model_id="claude-haiku-5-5")
        self.luna = AIModel.objects.get(model_id="gpt-6-luna")

    def ask(self, model, response):
        with mock.patch("requests.post", return_value=response) as post:
            reply = clients.ask(model, "be brief", "Hello", purpose="test")
        return reply, post.call_args

    def test_anthropic_request_and_answer(self):
        reply, call = self.ask(self.haiku, FakeResponse(ANTHROPIC_OK))
        self.assertEqual(call.args[0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(call.kwargs["headers"]["x-api-key"], "sk-test")
        self.assertEqual(call.kwargs["json"]["model"], "claude-haiku-5-5")
        self.assertEqual(call.kwargs["json"]["system"], "be brief")
        self.assertEqual(reply.text, "Bonjour")
        self.assertEqual((reply.input_tokens, reply.output_tokens), (1000, 200))

    def test_openai_request_and_answer(self):
        reply, call = self.ask(self.luna, FakeResponse(OPENAI_OK))
        self.assertEqual(call.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer sk-test")
        body = call.kwargs["json"]
        self.assertEqual((body["instructions"], body["store"]), ("be brief", False))
        self.assertEqual(body["input"], [{"role": "user", "content": [
            {"type": "input_text", "text": "Hello"}]}])
        self.assertEqual(reply.text, "Bonjour")

    def test_the_cost_is_priced_recorded_and_logged(self):
        # 1000 x $0.10 + 200 x $0.50 per 1M tokens = $0.0002 = 200 micro-USD
        reply, _ = self.ask(self.haiku, FakeResponse(ANTHROPIC_OK))
        self.assertEqual(reply.cost_micros, 200)
        self.assertEqual(budget.spent_today()["cost"], 200)
        self.assertTrue(ActivityLog.objects.filter(category="ai", event="test").exists())

    def test_a_provider_error_is_explained(self):
        error = FakeResponse({"error": {"message": "invalid x-api-key"}}, status=401)
        with self.assertRaisesMessage(clients.AIError, "HTTP 401: invalid x-api-key"):
            self.ask(self.haiku, error)

    def test_a_network_error_is_explained(self):
        import requests

        with mock.patch("requests.post",
                        side_effect=requests.ConnectionError("down")):
            with self.assertRaisesMessage(clients.AIError, "could not reach the provider"):
                clients.ask(self.haiku, "s", "p", purpose="test")

    def test_no_key_means_no_call(self):
        Provider.objects.update(api_key="")
        self.haiku.refresh_from_db()
        with mock.patch("requests.post") as post:
            with self.assertRaisesMessage(clients.AIError, "AI → Providers"):
                clients.ask(self.haiku, "s", "p", purpose="test")
        post.assert_not_called()

    def test_the_kill_switch_means_no_call(self):
        ControlPanel.objects.update_or_create(singleton=1, defaults={"paused": True})
        with mock.patch("requests.post") as post:
            with self.assertRaises(budget.PlatformPaused):
                clients.ask(self.haiku, "s", "p", purpose="test")
        post.assert_not_called()
        self.assertFalse(SpendLedger.objects.exists())

    def test_a_custom_base_url_is_used(self):
        Provider.objects.filter(kind="openai").update(base_url="https://proxy.example.com/")
        self.luna.refresh_from_db()
        _, call = self.ask(self.luna, FakeResponse(OPENAI_OK))
        self.assertEqual(call.args[0], "https://proxy.example.com/v1/responses")


class ModelTests(TestCase):
    def test_the_seeded_models(self):
        rows = {model.model_id: model for model in AIModel.objects.all()}
        self.assertEqual(sorted(rows), [
            "claude-haiku-5-5", "claude-sonnet-5-5", "gpt-4o-mini-transcribe", "gpt-4o-mini-tts",
            "gpt-6-astra", "gpt-6-luna", "gpt-6.1-sol", "gpt-image-2.5-flare",
            "gpt-image-2.5-sunburst", "gpt-transcribe"])
        self.assertEqual(AIModel.translation_default().model_id, "gpt-6-luna")
        self.assertEqual(rows["gpt-6.1-sol"].input_price, Decimal("2.0000"))
        self.assertEqual((rows["gpt-image-2.5-flare"].image_input_price,
                          rows["gpt-image-2.5-flare"].output_price),
                         (Decimal("8.0000"), Decimal("30.0000")))
        self.assertEqual(rows["gpt-transcribe"].unit_price, Decimal("0.0045"))
        defaults = {model.use: model.model_id for model in rows.values() if model.is_default}
        self.assertEqual(defaults, {"chat": "gpt-6-luna", "image": "gpt-image-2.5-flare",
                                    "transcription": "gpt-transcribe",
                                    "speech": "gpt-4o-mini-tts"})
        self.assertTrue(all(model.offered and model.priced for model in rows.values()))

    def test_only_one_default_for_translation(self):
        sol = AIModel.objects.get(model_id="gpt-6.1-sol")
        sol.default_for_translation = True
        sol.save()
        self.assertEqual(AIModel.translation_default(), sol)
        self.assertEqual(AIModel.objects.filter(default_for_translation=True).count(), 1)

    def test_one_default_for_users_per_use(self):
        sol = AIModel.objects.get(model_id="gpt-6.1-sol")
        sol.is_default = True
        sol.save()
        self.assertEqual(list(AIModel.objects.filter(use="chat", is_default=True)), [sol])
        self.assertTrue(AIModel.objects.get(model_id="gpt-image-2.5-flare").is_default)

    def test_users_have_only_models_with_a_key_except_in_demo(self):
        self.assertEqual(AIModel.offered_for("chat"), [])
        self.assertEqual(AIModel.default_for("chat", demo=True).model_id, "gpt-6-luna")
        Provider.objects.filter(kind="openai").update(api_key="sk-test")
        self.assertEqual([model.model_id for model in AIModel.offered_for("chat")],
                         ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"])
        AIModel.objects.filter(model_id="gpt-6-luna").update(offered=False)
        self.assertEqual(AIModel.default_for("chat").model_id, "gpt-6.1-sol")
        AIModel.objects.filter(model_id="gpt-6.1-sol").update(output_price=0)
        self.assertEqual(AIModel.default_for("chat").model_id, "gpt-6-astra")

    def test_costs(self):
        rows = {model.model_id: model for model in AIModel.objects.all()}
        # 1000 x $5 + 2000 x $8 + 4000 x $30 per 1M tokens = $0.141
        self.assertEqual(rows["gpt-image-2.5-flare"].cost_micros(1000, 4000, 2000), 141_000)
        # Two minutes at $0.0045 a minute; at least a second.
        self.assertEqual(rows["gpt-transcribe"].audio_cost_micros(120), 9000)
        self.assertEqual(rows["gpt-transcribe"].audio_cost_micros(0), 75)
        # 1,000 characters at $20 per 1M.
        self.assertEqual(rows["gpt-4o-mini-tts"].speech_cost_micros(1000), 20_000)


class AdminTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(user)

    def test_the_ai_pages_load(self):
        for url in ("/admin/ai/provider/", "/admin/ai/aimodel/",
                    f"/admin/ai/provider/{Provider.objects.first().pk}/change/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_the_key_is_never_shown_and_empty_keeps_it(self):
        provider = Provider.objects.get(kind="anthropic")
        url = f"/admin/ai/provider/{provider.pk}/change/"
        data = {"kind": "anthropic", "api_key": "sk-ant-secret-1234", "base_url": "",
                "enabled": "on", "models-TOTAL_FORMS": "0", "models-INITIAL_FORMS": "0"}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertNotContains(self.client.get(url), "sk-ant-secret-1234")
        self.assertContains(self.client.get("/admin/ai/provider/"), "••••1234")

        data["api_key"] = ""
        self.client.post(url, data)
        provider.refresh_from_db()
        self.assertEqual(provider.api_key, "sk-ant-secret-1234")

    def test_the_test_action_reports(self):
        Provider.objects.update(api_key="sk-test")
        with mock.patch("requests.post",
                        return_value=FakeResponse(ANTHROPIC_OK)):
            response = self.client.post("/admin/ai/provider/", {
                "action": "test",
                "_selected_action": [Provider.objects.get(kind="anthropic").pk]}, follow=True)
        self.assertContains(response, "Anthropic works")


class ImageTests(TestCase):
    """Photos go to the model before the prompt, as each provider expects them."""

    def setUp(self):
        Provider.objects.update(api_key="sk-test")

    def sent(self, model_id, body):
        model = AIModel.objects.get(model_id=model_id)
        with mock.patch("requests.post",
                        return_value=FakeResponse(body)) as post:
            clients.ask(model, "s", "Is this fine?", purpose="test",
                        images=[(b"\xff\xd8jpeg", "image/jpeg")])
        return post.call_args.kwargs["json"]

    def test_anthropic_gets_a_base64_image_block(self):
        content = self.sent("claude-haiku-5-5", ANTHROPIC_OK)["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": "/9hqcGVn"}})
        self.assertEqual(content[1], {"type": "text", "text": "Is this fine?"})

    def test_openai_gets_a_data_url(self):
        content = self.sent("gpt-6-luna", OPENAI_OK)["input"][0]["content"]
        self.assertEqual(content[0], {"type": "input_image",
                                      "image_url": "data:image/jpeg;base64,/9hqcGVn"})
        self.assertEqual(content[1], {"type": "input_text", "text": "Is this fine?"})

    def test_one_default_for_safety(self):
        self.assertEqual(AIModel.safety_default().model_id, "gpt-6-luna")
        sol = AIModel.objects.get(model_id="gpt-6.1-sol")
        sol.default_for_safety = True
        sol.save()
        self.assertEqual(list(AIModel.objects.filter(default_for_safety=True)), [sol])


class FakeStream:
    """A server-sent event stream, as requests gives it with stream=True."""

    def __init__(self, events, status=200, body=None):
        self.status_code, self.events, self.body = status, events, body or {}
        self.encoding = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def json(self):
        return self.body

    def iter_lines(self, decode_unicode=False):
        assert self.encoding == "utf-8", "the stream must be read as UTF-8"
        for event in self.events:
            yield "" if event is None else (
                event if isinstance(event, str) else "data: " + json.dumps(event))


def openai_events(*pieces, finish="completed", usage=None):
    """A Responses API stream: the text in pieces, then how it ended."""
    usage = usage or {"input_tokens": 1000, "output_tokens": 200}
    events = [{"type": "response.created", "response": {"status": "in_progress"}}, None]
    events += [{"type": "response.output_text.delta", "delta": piece} for piece in pieces]
    if finish == "length":
        events.append({"type": "response.incomplete", "response": {
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
            "usage": usage}})
    else:
        events.append({"type": "response.completed",
                       "response": {"status": "completed", "usage": usage}})
    return events


class ChatStreamTests(TestCase):
    def setUp(self):
        Provider.objects.update(api_key="sk-test")
        self.luna = AIModel.objects.get(model_id="gpt-6-luna")
        self.haiku = AIModel.objects.get(model_id="claude-haiku-5-5")

    def chat(self, model, stream, messages=None):
        seen = []
        with mock.patch("requests.post", return_value=stream) as post:
            reply = clients.chat(model, "be brief", messages or [
                {"role": "user", "text": "Hi", "images": []},
                {"role": "assistant", "text": "Hello!", "images": []},
                {"role": "user", "text": "Say salut", "images": [(b"\xff\xd8jpeg", "image/jpeg")],
                 "files": [{"name": "notes.pdf", "media_type": "application/pdf",
                            "data": b"%PDF-1.7"}]},
            ], purpose="chat", on_text=seen.append)
        return reply, seen, post.call_args

    def test_openai_streams_and_counts_the_tokens(self):
        reply, seen, call = self.chat(self.luna, FakeStream(openai_events("Sa", "lut ✨")))
        self.assertEqual((reply.text, reply.input_tokens, reply.output_tokens, reply.cut_off),
                         ("Salut ✨", 1000, 200, False))
        self.assertEqual(seen, ["Sa", "Salut ✨"])
        self.assertEqual(reply.cost_micros, 200)
        self.assertEqual(call.args[0], "https://api.openai.com/v1/responses")
        body = call.kwargs["json"]
        self.assertEqual((body["stream"], body["store"], body["max_output_tokens"],
                          body["instructions"]), (True, False, 4000, "be brief"))
        self.assertEqual([m["role"] for m in body["input"]], ["user", "assistant", "user"])
        self.assertEqual(body["input"][1]["content"], "Hello!")
        file, image, text = body["input"][2]["content"]
        self.assertEqual(file, {"type": "input_file", "filename": "notes.pdf",
                                "file_data": "data:application/pdf;base64,JVBERi0xLjc="})
        self.assertEqual(image, {"type": "input_image",
                                 "image_url": "data:image/jpeg;base64,/9hqcGVn"})
        self.assertEqual(text, {"type": "input_text", "text": "Say salut"})
        self.assertEqual(budget.spent_today()["cost"], 200)

    def test_an_answer_at_the_length_limit_is_marked(self):
        reply, _, _ = self.chat(self.luna, FakeStream(openai_events("Long", finish="length")))
        self.assertTrue(reply.cut_off)

    def test_a_stream_that_never_finishes_is_an_error(self):
        events = openai_events("Half")[:-1]
        with self.assertRaisesMessage(clients.AIError, "stopped before it was complete"):
            self.chat(self.luna, FakeStream(events))

    def test_a_failed_answer_is_explained(self):
        failed = [{"type": "response.failed",
                   "response": {"status": "failed", "error": {"message": "overloaded"}}}]
        with self.assertRaisesMessage(clients.AIError, "the answer failed: overloaded"):
            self.chat(self.luna, FakeStream(failed))

    def test_anthropic_reads_pdfs_and_text_files_as_documents(self):
        _, _, call = self.chat(self.haiku, FakeStream([
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
            {"type": "message_delta", "usage": {"output_tokens": 2}}]), messages=[
            {"role": "user", "text": "Compare", "images": [], "files": [
                {"name": "a.pdf", "media_type": "application/pdf", "data": b"%PDF-1.7"},
                {"name": "b.md", "media_type": "text/markdown", "data": "# Ünïcode".encode()}]}])
        pdf, text, question = call.kwargs["json"]["messages"][0]["content"]
        self.assertEqual(pdf, {"type": "document", "title": "a.pdf", "source": {
            "type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjc="}})
        self.assertEqual(text, {"type": "document", "title": "b.md", "source": {
            "type": "text", "media_type": "text/plain", "data": "# Ünïcode"}})
        self.assertEqual(question, {"type": "text", "text": "Compare"})

    def test_anthropic_refuses_what_is_not_text(self):
        with self.assertRaisesMessage(clients.AIError, "is not plain text"):
            self.chat(self.haiku, FakeStream([]), messages=[
                {"role": "user", "text": "Read", "images": [], "files": [
                    {"name": "x.docx", "media_type": "application/msword",
                     "data": b"PK\x03\x04\xff\xfe"}]}])

    def test_anthropic_streams_too(self):
        reply, seen, call = self.chat(self.haiku, FakeStream([
            {"type": "message_start", "message": {"usage": {"input_tokens": 1000}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Sa"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lut"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
             "usage": {"output_tokens": 200}},
            {"type": "message_stop"}]))
        self.assertEqual((reply.text, reply.input_tokens, reply.output_tokens), ("Salut", 1000, 200))
        self.assertEqual(seen, ["Sa", "Salut"])
        self.assertEqual(call.args[0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(call.kwargs["json"]["system"], "be brief")
        self.assertTrue(call.kwargs["json"]["stream"])

    def test_errors_are_explained(self):
        with self.assertRaisesMessage(clients.AIError, "HTTP 429: slow down"):
            self.chat(self.luna, FakeStream([], status=429,
                                            body={"error": {"message": "slow down"}}))
        with self.assertRaisesMessage(clients.AIError, "stream error: overloaded"):
            self.chat(self.haiku, FakeStream([{"type": "error",
                                               "error": {"message": "overloaded"}}]))

    def test_the_spend_guard_sees_the_most_it_can_cost(self):
        ControlPanel.objects.update_or_create(singleton=1, defaults={"hourly_budget": "0.01"})
        with mock.patch("requests.post") as post:
            with self.assertRaises(budget.CapExceeded):
                clients.chat(self.luna, "s", [{"role": "user", "text": "x", "images": []}],
                             purpose="chat", estimate_micros=20_000)
        post.assert_not_called()


class MediaResponse:
    def __init__(self, body=None, content=b"", status=200):
        self.body, self.content, self.status_code = body, content, status

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


class OpenAIMediaTests(TestCase):
    def setUp(self):
        Provider.objects.update(api_key="sk-test")
        self.flare = AIModel.objects.get(model_id="gpt-image-2.5-flare")
        self.stt = AIModel.objects.get(model_id="gpt-transcribe")
        self.tts = AIModel.objects.get(model_id="gpt-4o-mini-tts")

    IMAGE = {"data": [{"b64_json": "aW1hZ2U="}], "usage": {
        "input_tokens": 1100, "output_tokens": 4000,
        "input_tokens_details": {"text_tokens": 100, "image_tokens": 1000}}}

    def test_an_image_is_made_and_priced_from_its_usage(self):
        with mock.patch("requests.post",
                        return_value=MediaResponse(self.IMAGE)) as post:
            picture = clients.image(self.flare, "a red fox", "1024x1024", "high")
        self.assertEqual(picture.data, b"image")
        # 100 x $5 + 1000 x $8 + 4000 x $30 per 1M = $0.1285
        self.assertEqual(picture.cost_micros, 128_500)
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/images/generations")
        self.assertEqual(post.call_args.kwargs["json"], {
            "model": "gpt-image-2.5-flare", "prompt": "a red fox", "size": "1024x1024",
            "quality": "high", "n": 1})

    def test_a_photo_is_changed_through_edits(self):
        with mock.patch("requests.post",
                        return_value=MediaResponse(self.IMAGE)) as post:
            clients.image(self.flare, "make it night", "auto", "medium",
                          photos=[(b"\xff\xd8jpeg", "image/jpeg")])
        call = post.call_args
        self.assertEqual(call.args[0], "https://api.openai.com/v1/images/edits")
        self.assertEqual(call.kwargs["files"],
                         [("image", ("photo1.jpg", b"\xff\xd8jpeg", "image/jpeg"))])
        self.assertEqual(call.kwargs["data"]["size"], "auto")

    def test_without_usage_the_estimate_is_charged(self):
        with mock.patch("requests.post",
                        return_value=MediaResponse({"data": [{"b64_json": "aW1hZ2U="}]})):
            picture = clients.image(self.flare, "a fox", "1024x1024", "low",
                                    estimate_micros=5000)
        self.assertEqual(picture.cost_micros, 5000)

    def test_openais_safety_system_is_a_refusal(self):
        refused = MediaResponse({"error": {"message": "Your request was rejected by the "
                                           "safety system.", "code": "moderation_blocked"}},
                                status=400)
        with mock.patch("requests.post", return_value=refused):
            with self.assertRaises(clients.ProviderRefused):
                clients.image(self.flare, "x", "1024x1024", "low")

    def test_a_voice_note_is_transcribed_and_priced_by_its_length(self):
        with mock.patch("requests.post",
                        return_value=MediaResponse({"text": " Salut! "})) as post:
            text, cost = clients.transcribe(self.stt, b"OggS", "voice.ogg", 40)
        self.assertEqual((text, cost), ("Salut!", 3000))
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/audio/transcriptions")
        self.assertEqual(post.call_args.kwargs["files"], {"file": ("voice.ogg", b"OggS")})
        self.assertEqual(post.call_args.kwargs["data"]["model"], "gpt-transcribe")
        self.assertEqual(budget.spent_today()["cost"], 3000)

    def test_a_text_is_read_aloud_as_a_voice_note(self):
        with mock.patch("requests.post",
                        return_value=MediaResponse(content=b"OggS-audio")) as post:
            audio, cost = clients.speak(self.tts, "Hello there", "marin")
        self.assertEqual((audio, cost), (b"OggS-audio", 220))
        self.assertEqual(post.call_args.kwargs["json"], {
            "model": "gpt-4o-mini-tts", "input": "Hello there", "voice": "marin",
            "response_format": "opus"})

    def test_only_openai_makes_media(self):
        haiku = AIModel.objects.get(model_id="claude-haiku-5-5")
        with self.assertRaisesMessage(clients.AIError, "does not make images"):
            clients.image(haiku, "x", "1024x1024", "low")


class SyncTests(TestCase):
    def setUp(self):
        Provider.objects.update(api_key="sk-test")
        self.openai = Provider.objects.get(kind="openai")

    def sync(self, ids):
        from apps.ai import sync

        with mock.patch("requests.get", return_value=MediaResponse(
                {"data": [{"id": model_id} for model_id in ids]})) as get:
            result = sync.sync(self.openai)
        self.call = get.call_args
        return result

    def test_new_models_are_added_switched_off_and_known_ones_marked(self):
        result = self.sync(["gpt-6-luna", "gpt-6.1-sol", "gpt-7-nova", "gpt-7-nova-2026-11-01",
                            "text-embedding-4", "gpt-image-3", "gpt-5-transcribe"])
        self.assertEqual(result["added"], ["gpt-5-transcribe", "gpt-7-nova", "gpt-image-3"])
        self.assertIn("gpt-6-astra", result["missing"])
        new = {model.model_id: model for model in AIModel.objects.filter(
            model_id__in=result["added"])}
        self.assertEqual({model_id: model.use for model_id, model in new.items()}, {
            "gpt-5-transcribe": "transcription", "gpt-7-nova": "chat", "gpt-image-3": "image"})
        self.assertFalse(any(model.enabled or model.offered for model in new.values()))
        self.assertTrue(AIModel.objects.get(model_id="gpt-6-luna").listed)
        self.assertFalse(AIModel.objects.get(model_id="gpt-6-astra").listed)
        self.assertEqual(self.call.args[0], "https://api.openai.com/v1/models")
        self.openai.refresh_from_db()
        self.assertIsNotNone(self.openai.synced_at)

    def test_the_admin_action_reports(self):
        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(user)
        with mock.patch("requests.get", return_value=MediaResponse(
                {"data": [{"id": "gpt-6-luna"}, {"id": "gpt-7-nova"}]})):
            response = self.client.post("/admin/ai/provider/", {
                "action": "sync_models", "_selected_action": [self.openai.pk]}, follow=True)
        self.assertContains(response, "2 model(s) listed")
        self.assertContains(response, "gpt-7-nova")
        self.assertContains(response, "does not list")


class CacheTests(TestCase):
    """The providers' prompt cache: what is asked for, and what it costs."""

    def setUp(self):
        Provider.objects.update(api_key="sk-test")
        self.sol = AIModel.objects.get(model_id="gpt-6.1-sol")
        self.haiku = AIModel.objects.get(model_id="claude-haiku-5-5")

    def chat(self, model, stream, cache_key=""):
        with mock.patch("requests.post", return_value=stream) as post:
            reply = clients.chat(model, "be brief", [{"role": "user", "text": "Hi", "images": []}],
                                 purpose="chat", on_text=lambda text: None, cache_key=cache_key)
        return reply, post.call_args.kwargs["json"]

    def test_openai_reads_and_writes_are_priced_apart(self):
        # 15,000 in: 12,000 read from the cache, 3,000 written to it; 200 out.
        usage = {"input_tokens": 15000, "output_tokens": 200,
                 "input_tokens_details": {"cached_tokens": 12000, "cache_write_tokens": 3000}}
        reply, body = self.chat(self.sol, FakeStream(openai_events("Hi", usage=usage)),
                                cache_key="aistars-chat-7")
        self.assertEqual(body["prompt_cache_key"], "aistars-chat-7")
        self.assertEqual((reply.input_tokens, reply.cached_tokens, reply.written_tokens),
                         (15000, 12000, 3000))
        # Sol: $2 in, $10 out per 1M; reads 0.05x, writes 1.25x.
        self.assertEqual(reply.cost_micros, round(12000 * 2 * 0.05 + 3000 * 2 * 1.25 + 200 * 10))

    def test_no_key_no_cache_key(self):
        _reply, body = self.chat(self.sol, FakeStream(openai_events("Hi")))
        self.assertNotIn("prompt_cache_key", body)

    def test_anthropic_caches_when_asked_and_counts_all_the_input(self):
        reply, body = self.chat(self.haiku, FakeStream([
            {"type": "message_start", "message": {"usage": {
                "input_tokens": 50, "cache_read_input_tokens": 10000,
                "cache_creation_input_tokens": 2000}}},
            {"type": "message_delta", "usage": {"output_tokens": 100}}]), cache_key="k")
        self.assertEqual(body["cache_control"], {"type": "ephemeral"})
        self.assertEqual((reply.input_tokens, reply.cached_tokens, reply.written_tokens),
                         (12050, 10000, 2000))
        # Haiku: $0.10 in, $0.50 out per 1M; reads 0.1x, writes 1.25x.
        self.assertEqual(reply.cost_micros, round(50 * 0.1 + 10000 * 0.1 * 0.1
                                                  + 2000 * 0.1 * 1.25 + 100 * 0.5))
        _reply, body = self.chat(self.haiku, FakeStream([]))
        self.assertNotIn("cache_control", body)

    def test_the_seeded_factors(self):
        self.assertEqual((self.sol.cache_read_factor, self.sol.cache_write_factor),
                         (Decimal("0.050"), Decimal("1.250")))
        self.assertEqual(self.haiku.cache_read_factor, Decimal("0.100"))
