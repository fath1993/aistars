"""
Calling the AI providers. One function per kind of work:

    ask          one question, one answer (translation, the safety judge)
    chat         a conversation, with photos and files, streamed as it is
                 written (the bot's Chat); OpenAI's through its Responses API
    image        an image made from a prompt, or a photo changed
    transcribe   a voice note turned into text
    speak        a text read aloud, as a voice note

Every call:
  - is refused while the Control panel's kill switch is on or a USD cap is
    reached;
  - is priced from what the provider reports (tokens) or from what was sent
    (seconds of audio, characters), with the model's prices, and recorded in
    the spend ledger;
  - is written to the activity log, with its cost.

The providers are called over plain HTTP (requests), not their SDKs: a few
small JSON APIs do not justify more dependencies. Images, voice to text and
text to speech are OpenAI's; Anthropic's models chat.
"""

import base64
import json
import logging
from dataclasses import dataclass

import requests

from .models import ProviderKind

logger = logging.getLogger(__name__)

TIMEOUT = 120
# Streaming: the connection, then the longest silence between two pieces
# (a reasoning model may think for a while before its first word).
STREAM_TIMEOUT = (15, 180)
IMAGE_TIMEOUT = 300
ANTHROPIC_VERSION = "2023-06-01"


class AIError(Exception):
    """The call did not produce an answer; the message says why."""


class ProviderRefused(AIError):
    """The provider's own safety system refused the request."""


@dataclass
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    cost_micros: int
    # Chat: the answer stopped at the model's length limit.
    cut_off: bool = False


@dataclass
class Picture:
    data: bytes
    input_tokens: int
    image_input_tokens: int
    output_tokens: int
    cost_micros: int


def _guard(model, estimate_micros=0):
    from apps.core.services import budget

    if not model.enabled:
        raise AIError(f"the model {model} is switched off")
    if not model.provider.usable:
        raise AIError(f"{model.provider} has no API key, or is switched off: "
                      "AI → Providers")
    budget.assert_can_spend(estimate_micros)


def _record(model, purpose, cost, message, detail):
    from apps.core import activity
    from apps.core.models import LogCategory
    from apps.core.services import budget

    budget.record_spend(cost)
    activity.info(LogCategory.AI, purpose, f"{model}: {message}, ${cost / 1e6:.4f}",
                  detail={"model": model.model_id, "cost_micros": cost, **detail})


def _headers(model):
    key = model.provider.api_key.strip()
    if model.provider.kind == ProviderKind.ANTHROPIC:
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {key}"}


def _error(response):
    """The provider's own words for a failed call, as an AIError."""
    try:
        body = response.json()
    except ValueError:
        return AIError(f"HTTP {response.status_code}, not JSON")
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    code = error.get("code") if isinstance(error, dict) else ""
    text = f"HTTP {response.status_code}: {message or body}"
    if code == "moderation_blocked" or "safety system" in str(message or ""):
        return ProviderRefused(text)
    return AIError(text)


def _post(url, headers, payload=None, timeout=TIMEOUT, files=None, data=None):
    try:
        response = requests.post(url, headers=headers, json=payload, files=files, data=data,
                                 timeout=timeout)
    except requests.RequestException as exc:
        raise AIError(f"could not reach the provider: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise _error(response)
    return response


def _json(response):
    try:
        return response.json()
    except ValueError:
        raise AIError(f"HTTP {response.status_code}, not JSON") from None


def _encoded(data) -> str:
    return base64.b64encode(data).decode("ascii")


# --------------------------------------------------------------------------
# One question, one answer
# --------------------------------------------------------------------------

def ask(model, system: str, prompt: str, purpose: str, max_tokens: int = 4000,
        images=()) -> Reply:
    """
    Ask `model` once. `images` are (bytes, media type) pairs shown before the
    prompt. Raises AIError, or the spend guard's BudgetError.
    """
    return chat(model, system, [{"role": "user", "text": prompt, "images": list(images)}],
                purpose, max_tokens=max_tokens)


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------

def chat(model, system, messages, purpose, max_tokens=None, on_text=None,
         estimate_micros=0) -> Reply:
    """
    A conversation: `messages` are {"role": "user" | "assistant", "text",
    "images": [(bytes, media type)], "files": [{"name", "media_type",
    "data"}]}, oldest first. With `on_text`, the answer is streamed and
    on_text(text so far) is called as it grows.
    """
    _guard(model, estimate_micros)
    max_tokens = max_tokens or model.max_output_tokens
    if model.provider.kind == ProviderKind.ANTHROPIC:
        text, tokens_in, tokens_out, cut = _anthropic(model, system, messages, max_tokens,
                                                      on_text)
    else:
        text, tokens_in, tokens_out, cut = _openai(model, system, messages, max_tokens,
                                                   on_text)
    cost = model.cost_micros(tokens_in, tokens_out)
    _record(model, purpose, cost, f"{tokens_in} in, {tokens_out} out",
            {"input_tokens": tokens_in, "output_tokens": tokens_out})
    return Reply(text, tokens_in, tokens_out, cost, cut)


def _data_url(media_type, data) -> str:
    return f"data:{media_type};base64,{_encoded(data)}"


def _openai_content(message):
    """
    The Responses API's input: an answer as plain text; the user's files
    and photos before their words.
    """
    if message["role"] == "assistant":
        return message["text"]
    parts = [{"type": "input_file", "filename": item["name"],
              "file_data": _data_url(item["media_type"], item["data"])}
             for item in message.get("files") or []]
    parts += [{"type": "input_image", "image_url": _data_url(media_type, data)}
              for data, media_type in message.get("images") or []]
    return parts + [{"type": "input_text", "text": message["text"]}]


def _openai(model, system, messages, max_tokens, on_text):
    """
    OpenAI's Responses API: the one that reads every kind of file (Chat
    Completions reads only PDFs). Nothing is stored at OpenAI (store false).
    """
    payload = {
        "model": model.model_id,
        "instructions": system,
        "input": [{"role": message["role"], "content": _openai_content(message)}
                  for message in messages],
        "max_output_tokens": max_tokens,
        "store": False,
    }
    url = f"{model.provider.endpoint}/v1/responses"
    if on_text is None:
        return _openai_result(_json(_post(url, _headers(model), payload)))

    payload["stream"] = True
    text, result = "", None
    for event in _events(url, _headers(model), payload):
        kind = event.get("type")
        if kind == "response.output_text.delta":
            text += event.get("delta") or ""
            on_text(text)
        elif kind in ("response.completed", "response.incomplete"):
            result = event.get("response") or {}
        elif kind == "response.failed":
            error = (event.get("response") or {}).get("error") or {}
            raise AIError(f"the answer failed: {error.get('message') or error}")
        elif kind == "error":
            raise AIError(f"stream error: {event.get('message') or event}")
    if result is None:
        raise AIError("the answer stopped before it was complete")
    usage = result.get("usage") or {}
    return (text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
            _cut_off(result))


def _openai_result(body):
    text = "".join(part.get("text", "") for item in body.get("output") or []
                   if item.get("type") == "message"
                   for part in item.get("content") or [] if part.get("type") == "output_text")
    usage = body.get("usage") or {}
    return (text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
            _cut_off(body))


def _cut_off(response) -> bool:
    details = response.get("incomplete_details") or {}
    return response.get("status") == "incomplete" and details.get("reason") == "max_output_tokens"


def _anthropic_content(message):
    """Photos as images, PDFs as documents, text files as text documents."""
    if not message.get("images") and not message.get("files"):
        return message["text"]
    parts = []
    for item in message.get("files") or []:
        if item["media_type"] == "application/pdf":
            source = {"type": "base64", "media_type": item["media_type"],
                      "data": _encoded(item["data"])}
        else:
            try:
                source = {"type": "text", "media_type": "text/plain",
                          "data": item["data"].decode("utf-8")}
            except UnicodeDecodeError:
                raise AIError(f"{item['name']} is not plain text, which is all "
                              "Anthropic's models read besides PDFs") from None
        parts.append({"type": "document", "source": source, "title": item["name"]})
    parts += [{"type": "image", "source": {"type": "base64", "media_type": media_type,
                                           "data": _encoded(data)}}
              for data, media_type in message.get("images") or []]
    return parts + [{"type": "text", "text": message["text"]}]


def _anthropic(model, system, messages, max_tokens, on_text):
    payload = {
        "model": model.model_id, "max_tokens": max_tokens, "system": system,
        "messages": [{"role": message["role"], "content": _anthropic_content(message)}
                     for message in messages],
    }
    url = f"{model.provider.endpoint}/v1/messages"
    if on_text is None:
        body = _json(_post(url, _headers(model), payload))
        text = "".join(part.get("text", "") for part in body.get("content", [])
                       if part.get("type") == "text")
        usage = body.get("usage", {})
        return (text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
                body.get("stop_reason") == "max_tokens")

    payload["stream"] = True
    text, tokens_in, tokens_out, stop = "", 0, 0, ""
    for event in _events(url, _headers(model), payload):
        kind = event.get("type")
        if kind == "error":
            raise AIError(f"stream error: {(event.get('error') or {}).get('message', event)}")
        if kind == "message_start":
            usage = (event.get("message") or {}).get("usage") or {}
            tokens_in = int(usage.get("input_tokens", 0))
        elif kind == "content_block_delta":
            piece = (event.get("delta") or {}).get("text") or ""
            if piece:
                text += piece
                on_text(text)
        elif kind == "message_delta":
            tokens_out = int((event.get("usage") or {}).get("output_tokens", tokens_out))
            stop = (event.get("delta") or {}).get("stop_reason") or stop
    return text, tokens_in, tokens_out, stop == "max_tokens"


def _events(url, headers, payload):
    """The JSON objects of a server-sent event stream."""
    try:
        response = requests.post(url, headers=headers, json=payload, stream=True,
                                 timeout=STREAM_TIMEOUT)
    except requests.RequestException as exc:
        raise AIError(f"could not reach the provider: {type(exc).__name__}") from exc
    with response:
        if response.status_code != 200:
            raise _error(response)
        # text/event-stream usually names no charset, and requests would then
        # read it as Latin-1: every non-English answer would come out garbled.
        response.encoding = "utf-8"
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except ValueError:
                    continue
        except requests.RequestException as exc:
            raise AIError(f"the answer stopped coming: {type(exc).__name__}") from exc


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def image(model, prompt, size, quality, photos=(), purpose="image",
          estimate_micros=0) -> Picture:
    """
    Make one image from `prompt`, or change `photos` ([(bytes, media type)])
    by it. Priced from the tokens OpenAI reports; without them, the estimate.
    Raises ProviderRefused when OpenAI's own safety system says no.
    """
    _guard(model, estimate_micros)
    if model.provider.kind != ProviderKind.OPENAI:
        raise AIError(f"{model.provider} does not make images here")
    headers = _headers(model)
    options = {"model": model.model_id, "prompt": prompt, "size": size, "quality": quality,
               "n": 1}
    if photos:
        field = "image" if len(photos) == 1 else "image[]"
        files = [(field, (f"photo{number}.jpg", data, media_type))
                 for number, (data, media_type) in enumerate(photos, 1)]
        response = _post(f"{model.provider.endpoint}/v1/images/edits", headers,
                         files=files, data={k: str(v) for k, v in options.items()},
                         timeout=IMAGE_TIMEOUT)
    else:
        response = _post(f"{model.provider.endpoint}/v1/images/generations", headers,
                         options, timeout=IMAGE_TIMEOUT)
    body = _json(response)
    try:
        data = base64.b64decode(body["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise AIError("no image in the answer") from None
    usage = body.get("usage") or {}
    details = usage.get("input_tokens_details") or {}
    tokens_in = int(details.get("text_tokens", usage.get("input_tokens", 0)) or 0)
    image_in = int(details.get("image_tokens", 0) or 0)
    tokens_out = int(usage.get("output_tokens", 0) or 0)
    cost = model.cost_micros(tokens_in, tokens_out, image_in) if usage else estimate_micros
    _record(model, purpose, cost, f"{size} {quality}, {tokens_in}+{image_in} in, "
            f"{tokens_out} out", {"input_tokens": tokens_in, "image_input_tokens": image_in,
                                  "output_tokens": tokens_out})
    return Picture(data, tokens_in, image_in, tokens_out, cost)


# --------------------------------------------------------------------------
# Voice
# --------------------------------------------------------------------------

def transcribe(model, audio, filename, seconds, purpose="transcription") -> tuple:
    """(text, cost) of a voice note or audio file of `seconds`."""
    cost = model.audio_cost_micros(seconds)
    _guard(model, cost)
    if model.provider.kind != ProviderKind.OPENAI:
        raise AIError(f"{model.provider} does not transcribe here")
    response = _post(f"{model.provider.endpoint}/v1/audio/transcriptions", _headers(model),
                     files={"file": (filename, audio)},
                     data={"model": model.model_id, "response_format": "json"})
    text = str(_json(response).get("text") or "").strip()
    _record(model, purpose, cost, f"{seconds:.0f} s of audio", {"seconds": seconds})
    return text, cost


def speak(model, text, voice, purpose="speech") -> tuple:
    """(Ogg Opus bytes, cost): a voice note Telegram plays as one."""
    cost = model.speech_cost_micros(len(text))
    _guard(model, cost)
    if model.provider.kind != ProviderKind.OPENAI:
        raise AIError(f"{model.provider} does not speak here")
    response = _post(f"{model.provider.endpoint}/v1/audio/speech", _headers(model),
                     {"model": model.model_id, "input": text, "voice": voice,
                      "response_format": "opus"})
    if not response.content:
        raise AIError("no audio in the answer")
    _record(model, purpose, cost, f"{len(text)} characters read", {"characters": len(text)})
    return response.content, cost


# --------------------------------------------------------------------------
# The admin
# --------------------------------------------------------------------------

def list_models(provider) -> list:
    """The model IDs the provider says this key can use. Raises AIError."""
    key = provider.api_key.strip()
    if not key:
        raise AIError(f"{provider} has no API key")
    headers = ({"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
               if provider.kind == ProviderKind.ANTHROPIC else {"Authorization": f"Bearer {key}"})
    params = {"limit": 1000} if provider.kind == ProviderKind.ANTHROPIC else None
    try:
        response = requests.get(f"{provider.endpoint}/v1/models", headers=headers,
                                params=params, timeout=30)
    except requests.RequestException as exc:
        raise AIError(f"could not reach {provider}: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise _error(response)
    return sorted(str(row.get("id")) for row in _json(response).get("data") or []
                  if row.get("id"))


def test_provider(provider) -> tuple:
    """(ok, message) after one tiny call with the provider's first chat model."""
    from .models import Use

    model = provider.models.filter(enabled=True, use=Use.CHAT).first()
    if model is None:
        return False, f"{provider} has no enabled chat model to test with."
    try:
        reply = ask(model, "Answer with one word.", "Say OK.", "provider_test", max_tokens=200)
    except Exception as exc:  # AIError or a budget refusal: shown as is
        return False, f"{provider}: {exc}"
    return True, f"{provider} works ({model}: “{reply.text.strip()[:40]}”)."
