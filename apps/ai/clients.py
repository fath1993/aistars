"""
The core of every AI call: one function per thing the bot does.

    ask          one question, one answer (translation, the safety judge)
    chat         a conversation, with photos and files, streamed as it is
                 written (the bot's Chat)
    image        an image made from a prompt, or a photo changed
    transcribe   a voice note turned into text
    speak        a text read aloud, as a voice note

Every call:
  - goes to the model's provider through its adapter (adapters/), and is
    refused when that provider does not do it;
  - is refused while the Control panel's kill switch is on or a USD cap is
    reached;
  - is priced from what the provider reports (tokens) or from what was sent
    (seconds of audio, characters), with the model's prices, and recorded in
    the spend ledger and as an AICall;
  - is written to the activity log, with its cost.

Nothing here knows which provider it is: what each one does, and how, is its
adapter's.
"""

import logging
from dataclasses import dataclass

from . import adapters
from .adapters.base import AIError, ProviderRefused, Usage  # noqa: F401  (the core's errors)

logger = logging.getLogger(__name__)


@dataclass
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    cost_micros: int
    # Chat: the answer stopped at the model's length limit.
    cut_off: bool = False
    # Of input_tokens: read from the provider's cache, and written to it.
    cached_tokens: int = 0
    written_tokens: int = 0


@dataclass
class Picture:
    data: bytes
    input_tokens: int
    image_input_tokens: int
    output_tokens: int
    cost_micros: int


# What each core function is called when a provider does not do it.
DOES_NOT = {"chat": "chat", "image": "make images", "transcription": "transcribe",
            "speech": "speak"}


def _adapter(model, function):
    """The adapter that carries out `function` for `model`, or AIError."""
    adapter = adapters.of(model.provider)
    if function not in adapter.functions:
        raise AIError(f"{model.provider} does not {DOES_NOT[function]} here")
    return adapter


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

    from .models import AICall, Purpose

    budget.record_spend(cost)
    AICall.objects.create(
        provider=model.provider.kind, model=model.model_id,
        purpose=purpose if purpose in Purpose.values else Purpose.OTHER,
        cost_micros=max(int(cost), 0), input_tokens=int(detail.get("input_tokens", 0)),
        output_tokens=int(detail.get("output_tokens", 0)),
        cached_tokens=int(detail.get("cached_tokens", 0)), note=message[:200])
    activity.info(LogCategory.AI, purpose, f"{model}: {message}, ${cost / 1e6:.4f}",
                  detail={"model": model.model_id, "cost_micros": cost, **detail})


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
         estimate_micros=0, cache_key="") -> Reply:
    """
    A conversation: `messages` are {"role": "user" | "assistant", "text",
    "images": [(bytes, media type)], "files": [{"name", "media_type",
    "data"}]}, oldest first. With `on_text`, the answer is streamed and
    on_text(text so far) is called as it grows. `cache_key` (a chat's) keeps
    its start in the provider's cache, apart from other users' chats.
    """
    adapter = _adapter(model, "chat")
    _guard(model, estimate_micros)
    max_tokens = max_tokens or model.max_output_tokens
    text, usage, cut = adapter.chat(model, system, messages, max_tokens, on_text, cache_key)
    cost = model.cost_micros(usage.input, usage.output, cached=usage.cached,
                             written=usage.written)
    _record(model, purpose, cost,
            f"{usage.input} in ({usage.cached} cached, {usage.written} written), "
            f"{usage.output} out",
            {"input_tokens": usage.input, "cached_tokens": usage.cached,
             "written_tokens": usage.written, "output_tokens": usage.output})
    return Reply(text, usage.input, usage.output, cost, cut, usage.cached, usage.written)


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def image(model, prompt, size, quality, photos=(), purpose="image",
          estimate_micros=0) -> Picture:
    """
    Make one image from `prompt`, or change `photos` ([(bytes, media type)])
    by it. Priced from the tokens the provider reports; without them, the
    estimate. Raises ProviderRefused when the provider's own safety system
    says no.
    """
    adapter = _adapter(model, "image")
    _guard(model, estimate_micros)
    made = adapter.image(model, prompt, size, quality, list(photos))
    tokens_in, image_in, tokens_out = made.text_tokens, made.image_tokens, made.output_tokens
    cost = model.cost_micros(tokens_in, tokens_out, image_in) if made.reported \
        else estimate_micros
    _record(model, purpose, cost, f"{size} {quality}, {tokens_in}+{image_in} in, "
            f"{tokens_out} out", {"input_tokens": tokens_in, "image_input_tokens": image_in,
                                  "output_tokens": tokens_out})
    return Picture(made.data, tokens_in, image_in, tokens_out, cost)


# --------------------------------------------------------------------------
# Voice
# --------------------------------------------------------------------------

def transcribe(model, audio, filename, seconds, purpose="transcription") -> tuple:
    """(text, cost) of a voice note or audio file of `seconds`."""
    adapter = _adapter(model, "transcription")
    cost = model.audio_cost_micros(seconds)
    _guard(model, cost)
    text = adapter.transcribe(model, audio, filename)
    _record(model, purpose, cost, f"{seconds:.0f} s of audio", {"seconds": seconds})
    return text, cost


def speak(model, text, voice, purpose="speech") -> tuple:
    """(Ogg Opus bytes, cost): a voice note Telegram plays as one."""
    adapter = _adapter(model, "speech")
    cost = model.speech_cost_micros(len(text))
    _guard(model, cost)
    data = adapter.speak(model, text, voice)
    _record(model, purpose, cost, f"{len(text)} characters read", {"characters": len(text)})
    return data, cost


# --------------------------------------------------------------------------
# The admin
# --------------------------------------------------------------------------

def list_models(provider) -> list:
    """The model IDs the provider says this key can use. Raises AIError."""
    if not provider.api_key.strip():
        raise AIError(f"{provider} has no API key")
    return adapters.of(provider).list_models(provider)


def billed_costs(provider, start, end) -> int:
    """
    What the provider itself says was spent from `start` to `end`
    (datetimes), in micro-USD, read with its Admin key. Raises AIError (no
    Admin key, the provider does not report it, or it refused).
    """
    adapter = adapters.of(provider)
    if not adapter.reports_costs or not provider.admin_key.strip():
        raise AIError(f"{provider} has no Admin key")
    return adapter.billed_costs(provider, start, end)


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
