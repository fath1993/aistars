"""
Texts users read that live in the database rather than the code: the models'
taglines and the assistants' names, lines and first messages. They are
recorded with the bot's texts (location "catalog:…") whenever one changes,
so the AI translates them into every language with Translate on, and tr()
shows them in the user's language.
"""

import logging

from apps.core import i18n

logger = logging.getLogger(__name__)


def tr(text) -> str:
    """A database text in the active language ('' stays '')."""
    return i18n.gettext(text) if text else ""


def texts() -> dict:
    """text -> location, for every database text users can see."""
    from apps.ai.models import AIModel

    from .models import Assistant

    found = {}
    for model in AIModel.objects.filter(offered=True).exclude(tagline=""):
        found.setdefault(model.tagline, f"{i18n.CATALOG}model:{model.model_id}")
    for assistant in Assistant.objects.filter(active=True):
        for text in (assistant.name, assistant.description, assistant.first_message):
            if text:
                found.setdefault(text, f"{i18n.CATALOG}assistant:{assistant.slug}")
    return found


def record():
    """Record the texts now in use, and start translating the new ones."""
    new = i18n.record_catalog(texts())
    if new:
        from apps.core import tasks

        try:
            tasks.translate_missing.delay()
        except Exception:  # broker down during a deploy: the beat retries
            logger.warning("could not queue translations")
    return new
