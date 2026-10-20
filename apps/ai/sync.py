"""
Reading a provider's list of models (AI → Providers → Sync models).

The list says which model IDs the key can use, not what they cost: no
provider publishes prices through its API. So a model new to us is added
switched off and not offered, with its use guessed from its name; the owner
sets its prices and offers it. Models we already have are marked listed or
not, which tells a seeded model whose ID the provider does not know.
"""

import re

from django.utils import timezone

from . import clients
from .models import AIModel, Use

# Dated snapshots (gpt-6-luna-2026-09-29) duplicate the model they pin.
DATED = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{8})$")

# Families the bot has no use for.
SKIP = ("embedding", "moderation", "realtime", "audio-preview", "search", "davinci",
        "babbage", "dall-e", "sora", "computer-use", "codex", "gpt-live", "translate",
        "rosalind", "cyber", "chat-latest", "instruct", "tts-1")


def guess_use(model_id: str) -> str:
    name = model_id.lower()
    if "image" in name:
        return Use.IMAGE
    if "transcribe" in name or "whisper" in name:
        return Use.TRANSCRIPTION
    if "tts" in name:
        return Use.SPEECH
    return Use.CHAT


def wanted(model_id: str) -> bool:
    name = model_id.lower()
    return not DATED.search(name) and not any(part in name for part in SKIP)


def sync(provider) -> dict:
    """{"listed", "added", "missing"}: what the provider's list changed. Raises AIError."""
    ids = set(clients.list_models(provider))
    known = {model.model_id: model for model in provider.models.all()}
    for model_id, model in known.items():
        listed = model_id in ids
        if model.listed != listed:
            AIModel.objects.filter(pk=model.pk).update(listed=listed)
    added = []
    for model_id in sorted(ids - set(known)):
        if not wanted(model_id):
            continue
        AIModel.objects.create(provider=provider, model_id=model_id, label=model_id,
                               use=guess_use(model_id), enabled=False, offered=False,
                               listed=True)
        added.append(model_id)
    provider.synced_at = timezone.now()
    provider.save(update_fields=["synced_at"])
    return {"listed": len(ids), "added": added,
            "missing": sorted(model_id for model_id in known if model_id not in ids)}
