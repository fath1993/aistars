"""
Demo mode: every tool answers without calling any provider, so the whole bot
can be tried before an API key is set (testers only). Credits are held and
charged as usual, from what the request would have cost: the model's prices
on the request's real size.
"""

from pathlib import Path

from apps.ai.clients import Reply

SAMPLES = Path(__file__).resolve().parent / "samples"


def chat_reply(job, on_text) -> Reply:
    words = (f"🧪 Demo answer from {job.model}. In Demo mode nothing is sent to the AI. "
             f"You wrote: “{job.prompt[:200]}”").split(" ")
    text = ""
    for word in words:
        text = f"{text} {word}".strip()
        on_text(text)
    tokens_in, tokens_out = len(job.prompt) // 3 + 400, len(text) // 3
    return Reply(text, tokens_in, tokens_out, job.model.cost_micros(tokens_in, tokens_out))


def image() -> bytes:
    return (SAMPLES / "demo.jpg").read_bytes()


def voice() -> bytes:
    return (SAMPLES / "demo.ogg").read_bytes()


def transcript(seconds) -> str:
    return f"🧪 Demo transcript of a {int(seconds)}-second voice note."
