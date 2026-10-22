"""
OpenAI: chat (its Responses API), images, voice to text, text to speech;
also the free Moderation check, and its own cost figures with an Admin key.
"""

import base64

from . import base
from .base import AIError, Made, Usage

IMAGE_TIMEOUT = 300
MODERATION_MODEL = "omni-moderation-latest"
MODERATION_TIMEOUT = 30


def usage_of(usage) -> Usage:
    usage = usage or {}
    details = usage.get("input_tokens_details") or {}
    return Usage(int(usage.get("input_tokens", 0) or 0),
                 int(details.get("cached_tokens", 0) or 0),
                 int(details.get("cache_write_tokens", 0) or 0),
                 int(usage.get("output_tokens", 0) or 0))


class OpenAI(base.Adapter):
    kind = "openai"
    name = "OpenAI"
    default_url = "https://api.openai.com"
    functions = frozenset({"chat", "image", "transcription", "speech"})
    reads = frozenset({"pdf", "text", "office"})
    moderates = True
    reports_costs = True

    # ---- chat ---------------------------------------------------------------

    def chat(self, model, system, messages, max_tokens, on_text, cache_key):
        """
        The Responses API: the one that reads every kind of file (Chat
        Completions reads only PDFs). Nothing is stored at OpenAI (store false).
        """
        payload = {
            "model": model.model_id,
            "instructions": system,
            "input": [{"role": message["role"], "content": _content(message)}
                      for message in messages],
            "max_output_tokens": max_tokens,
            "store": False,
        }
        if cache_key:
            payload["prompt_cache_key"] = cache_key
        url = f"{model.provider.endpoint}/v1/responses"
        headers = self.auth(model.provider)
        if on_text is None:
            return _result(base.json_of(base.post(url, headers, payload)))

        payload["stream"] = True
        text, result = "", None
        for event in base.events(url, headers, payload):
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
        return text, usage_of(result.get("usage")), _cut_off(result)

    # ---- images -------------------------------------------------------------

    def image(self, model, prompt, size, quality, photos):
        headers = self.auth(model.provider)
        options = {"model": model.model_id, "prompt": prompt, "size": size,
                   "quality": quality, "n": 1}
        if photos:
            field = "image" if len(photos) == 1 else "image[]"
            files = [(field, (f"photo{number}.jpg", data, media_type))
                     for number, (data, media_type) in enumerate(photos, 1)]
            response = base.post(f"{model.provider.endpoint}/v1/images/edits", headers,
                                 files=files, data={k: str(v) for k, v in options.items()},
                                 timeout=IMAGE_TIMEOUT)
        else:
            response = base.post(f"{model.provider.endpoint}/v1/images/generations", headers,
                                 options, timeout=IMAGE_TIMEOUT)
        body = base.json_of(response)
        try:
            data = base64.b64decode(body["data"][0]["b64_json"])
        except (KeyError, IndexError, TypeError, ValueError):
            raise AIError("no image in the answer") from None
        usage = body.get("usage") or {}
        details = usage.get("input_tokens_details") or {}
        return Made(data,
                    int(details.get("text_tokens", usage.get("input_tokens", 0)) or 0),
                    int(details.get("image_tokens", 0) or 0),
                    int(usage.get("output_tokens", 0) or 0), reported=bool(usage))

    # ---- voice --------------------------------------------------------------

    def transcribe(self, model, audio, filename):
        response = base.post(f"{model.provider.endpoint}/v1/audio/transcriptions",
                             self.auth(model.provider), files={"file": (filename, audio)},
                             data={"model": model.model_id, "response_format": "json"})
        return str(base.json_of(response).get("text") or "").strip()

    def speak(self, model, text, voice):
        """Ogg Opus: a voice note Telegram plays as one."""
        response = base.post(f"{model.provider.endpoint}/v1/audio/speech",
                             self.auth(model.provider),
                             {"model": model.model_id, "input": text, "voice": voice,
                              "response_format": "opus"})
        if not response.content:
            raise AIError("no audio in the answer")
        return response.content

    # ---- the owner's side ---------------------------------------------------

    def moderate(self, provider, prompt, photos):
        """
        The prompt with the first photo, then each further photo on its own:
        Moderation reads one image a call.
        """
        calls = [[{"type": "text", "text": prompt}]]
        for number, photo in enumerate(photos):
            item = {"type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base.encoded(photo)}"}}
            if number == 0:
                calls[0].append(item)
            else:
                calls.append([item])
        flagged = []
        for inputs in calls:
            response = base.post(f"{provider.endpoint}/v1/moderations", self.auth(provider),
                                 {"model": MODERATION_MODEL, "input": inputs},
                                 timeout=MODERATION_TIMEOUT)
            try:
                result = base.json_of(response)["results"][0]
                bool(result["flagged"])
                flagged += [name for name, on in (result.get("categories") or {}).items()
                            if on and name not in flagged]
            except (KeyError, IndexError, TypeError, AttributeError):
                raise AIError("unreadable answer") from None
        return flagged

    def billed_costs(self, provider, start, end):
        """The Costs API, read with the Admin key."""
        key = provider.admin_key.strip()
        if not key:
            raise AIError(f"{provider} has no Admin key")
        total, page = 0.0, None
        for _ in range(12):   # at most a year of daily buckets, 31 a page
            params = {"start_time": int(start.timestamp()), "end_time": int(end.timestamp()),
                      "bucket_width": "1d", "limit": 31}
            if page:
                params["page"] = page
            body = base.json_of(base.get(f"{provider.endpoint}/v1/organization/costs",
                                         self.headers(key), params, timeout=base.TIMEOUT))
            for bucket in body.get("data") or []:
                for result in bucket.get("results") or []:
                    total += float((result.get("amount") or {}).get("value") or 0)
            page = body.get("next_page")
            if not page:
                break
        return round(total * 1_000_000)


def _content(message):
    """An answer as plain text; the user's files and photos before their words."""
    if message["role"] == "assistant":
        return message["text"]
    parts = [{"type": "input_file", "filename": item["name"],
              "file_data": base.data_url(item["media_type"], item["data"])}
             for item in message.get("files") or []]
    parts += [{"type": "input_image", "image_url": base.data_url(media_type, data)}
              for data, media_type in message.get("images") or []]
    return parts + [{"type": "input_text", "text": message["text"]}]


def _result(body):
    text = "".join(part.get("text", "") for item in body.get("output") or []
                   if item.get("type") == "message"
                   for part in item.get("content") or [] if part.get("type") == "output_text")
    return text, usage_of(body.get("usage")), _cut_off(body)


def _cut_off(response) -> bool:
    details = response.get("incomplete_details") or {}
    return response.get("status") == "incomplete" and details.get("reason") == "max_output_tokens"
