"""
Anthropic: chat (its Messages API), reading photos, PDFs and text files.
It makes no images and has no voice, so users who choose it are not offered
those.
"""

from . import base
from .base import AIError, Usage

VERSION = "2023-06-01"


def usage_of(usage, output=None) -> Usage:
    """Anthropic's input_tokens are only what was neither read nor written."""
    usage = usage or {}
    cached = int(usage.get("cache_read_input_tokens", 0) or 0)
    written = int(usage.get("cache_creation_input_tokens", 0) or 0)
    return Usage(int(usage.get("input_tokens", 0) or 0) + cached + written, cached, written,
                 int(usage.get("output_tokens", 0) or 0) if output is None else output)


class Anthropic(base.Adapter):
    kind = "anthropic"
    name = "Anthropic"
    default_url = "https://api.anthropic.com"
    functions = frozenset({"chat"})
    reads = frozenset({"pdf", "text"})

    def headers(self, key):
        return {"x-api-key": key, "anthropic-version": VERSION}

    def chat(self, model, system, messages, max_tokens, on_text, cache_key):
        payload = {
            "model": model.model_id, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": message["role"], "content": _content(message)}
                         for message in messages],
        }
        if cache_key:
            # Automatic caching: the breakpoint moves forward as the chat grows.
            payload["cache_control"] = {"type": "ephemeral"}
        url = f"{model.provider.endpoint}/v1/messages"
        headers = self.auth(model.provider)
        if on_text is None:
            body = base.json_of(base.post(url, headers, payload))
            text = "".join(part.get("text", "") for part in body.get("content", [])
                           if part.get("type") == "text")
            return text, usage_of(body.get("usage")), body.get("stop_reason") == "max_tokens"

        payload["stream"] = True
        text, started, tokens_out, stop = "", {}, 0, ""
        for event in base.events(url, headers, payload):
            kind = event.get("type")
            if kind == "error":
                raise AIError(f"stream error: {(event.get('error') or {}).get('message', event)}")
            if kind == "message_start":
                started = (event.get("message") or {}).get("usage") or {}
            elif kind == "content_block_delta":
                piece = (event.get("delta") or {}).get("text") or ""
                if piece:
                    text += piece
                    on_text(text)
            elif kind == "message_delta":
                tokens_out = int((event.get("usage") or {}).get("output_tokens", tokens_out))
                stop = (event.get("delta") or {}).get("stop_reason") or stop
        return text, usage_of(started, tokens_out), stop == "max_tokens"

    def list_models(self, provider):
        response = base.get(f"{provider.endpoint}/v1/models", self.auth(provider),
                            {"limit": 1000})
        return sorted(str(row.get("id")) for row in base.json_of(response).get("data") or []
                      if row.get("id"))


def _content(message):
    """Photos as images, PDFs as documents, text files as text documents."""
    if not message.get("images") and not message.get("files"):
        return message["text"]
    parts = []
    for item in message.get("files") or []:
        if item["media_type"] == "application/pdf":
            source = {"type": "base64", "media_type": item["media_type"],
                      "data": base.encoded(item["data"])}
        else:
            try:
                source = {"type": "text", "media_type": "text/plain",
                          "data": item["data"].decode("utf-8")}
            except UnicodeDecodeError:
                raise AIError(f"{item['name']} is not plain text, which is all "
                              "Anthropic's models read besides PDFs") from None
        parts.append({"type": "document", "source": source, "title": item["name"]})
    parts += [{"type": "image", "source": {"type": "base64", "media_type": media_type,
                                           "data": base.encoded(data)}}
              for data, media_type in message.get("images") or []]
    return parts + [{"type": "text", "text": message["text"]}]
