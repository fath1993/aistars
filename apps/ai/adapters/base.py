"""
What every provider adapter is, and the plain HTTP its calls share.

An adapter says which of the core functions its provider carries out
(`functions`, ai.models.Use values) and carries them out; anything it does not
declare, the base class refuses. The core (clients.py) prices, guards and
records every call; an adapter only speaks its provider's API.
"""

import base64
import json
from dataclasses import dataclass

import requests

TIMEOUT = 120
# Streaming: the connection, then the longest silence between two pieces
# (a reasoning model may think for a while before its first word).
STREAM_TIMEOUT = (15, 180)


class AIError(Exception):
    """The call did not produce an answer; the message says why."""


class ProviderRefused(AIError):
    """The provider's own safety system refused the request."""


@dataclass
class Usage:
    """Input in all, of it read from and written to the cache; output."""
    input: int = 0
    cached: int = 0
    written: int = 0
    output: int = 0


@dataclass
class Made:
    """An image as the provider gave it, and the tokens it reports (None: not reported)."""
    data: bytes
    text_tokens: int = 0
    image_tokens: int = 0
    output_tokens: int = 0
    reported: bool = True


class Adapter:
    """One provider. Subclasses set what it does and override those calls."""

    kind = ""
    name = ""
    default_url = ""
    # The core functions it carries out: chat, image, transcription, speech.
    functions = frozenset()
    # Files its chat models read besides photos: pdf, text, office.
    reads = frozenset()
    # A free moderation check of prompts and photos (the safety layer 2).
    moderates = False
    # Its own figure of what was spent, read with an Admin key (My finances).
    reports_costs = False

    def headers(self, key) -> dict:
        return {"Authorization": f"Bearer {key}"}

    def auth(self, provider) -> dict:
        return self.headers(provider.api_key.strip())

    # ---- the core functions ----------------------------------------------

    def chat(self, model, system, messages, max_tokens, on_text, cache_key) -> tuple:
        """(text, Usage, cut off at the length limit)."""
        raise AIError(f"{self.name} does not chat here")

    def image(self, model, prompt, size, quality, photos) -> Made:
        raise AIError(f"{self.name} does not make images here")

    def transcribe(self, model, audio, filename) -> str:
        raise AIError(f"{self.name} does not transcribe here")

    def speak(self, model, text, voice) -> bytes:
        raise AIError(f"{self.name} does not speak here")

    # ---- the owner's side ------------------------------------------------

    def list_models(self, provider) -> list:
        """The model IDs the provider says the key can use."""
        response = get(f"{provider.endpoint}/v1/models", self.auth(provider))
        return sorted(str(row.get("id")) for row in json_of(response).get("data") or []
                      if row.get("id"))

    def moderate(self, provider, prompt, photos) -> list:
        """The categories the provider flags in the prompt and the JPEG photos."""
        raise AIError(f"{self.name} has no moderation")

    def billed_costs(self, provider, start, end) -> int:
        """What the provider itself says was spent, in micro-USD."""
        raise AIError(f"{self.name} does not report its costs")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def error(response):
    """The provider's own words for a failed call, as an AIError."""
    try:
        body = response.json()
    except ValueError:
        return AIError(f"HTTP {response.status_code}, not JSON")
    found = body.get("error") if isinstance(body, dict) else None
    message = found.get("message") if isinstance(found, dict) else found
    code = found.get("code") if isinstance(found, dict) else ""
    text = f"HTTP {response.status_code}: {message or body}"
    if code == "moderation_blocked" or "safety system" in str(message or ""):
        return ProviderRefused(text)
    return AIError(text)


def post(url, headers, payload=None, timeout=TIMEOUT, files=None, data=None):
    try:
        response = requests.post(url, headers=headers, json=payload, files=files, data=data,
                                 timeout=timeout)
    except requests.RequestException as exc:
        raise AIError(f"could not reach the provider: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise error(response)
    return response


def get(url, headers, params=None, timeout=30):
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise AIError(f"could not reach the provider: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise error(response)
    return response


def json_of(response):
    try:
        return response.json()
    except ValueError:
        raise AIError(f"HTTP {response.status_code}, not JSON") from None


def events(url, headers, payload):
    """The JSON objects of a server-sent event stream."""
    try:
        response = requests.post(url, headers=headers, json=payload, stream=True,
                                 timeout=STREAM_TIMEOUT)
    except requests.RequestException as exc:
        raise AIError(f"could not reach the provider: {type(exc).__name__}") from exc
    with response:
        if response.status_code != 200:
            raise error(response)
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


def encoded(data) -> str:
    return base64.b64encode(data).decode("ascii")


def data_url(media_type, data) -> str:
    return f"data:{media_type};base64,{encoded(data)}"
