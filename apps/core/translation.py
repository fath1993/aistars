"""
Translating the bot's texts with AI, one language at a time.

The texts go to the model in batches as a JSON object of numbered texts, and
must come back as the same object translated. Every answer is checked before
it is saved: the placeholders kept, no HTML, not empty, not absurdly long. A
text that fails stays untranslated (English) and is reported.

Runs as a Celery task (tasks.translate_language), never in a web request.
"""

import json
import re

from django.db import transaction
from django.utils import timezone

from . import activity, i18n
from .models import Language, LogCategory, SourceText, Translation, TranslationStatus

BATCH = 40
PLACEHOLDER = re.compile(r"%\(\w+\)[sd]")
TAG = re.compile(r"<[a-zA-Z/!]")

SYSTEM = (
    "You translate the user interface of AIstars, a Telegram bot where people chat "
    "with AI, create images and turn voice into text, and pay with Telegram Stars. "
    "Translate from English into {native} ({name}, language code {code}).\n"
    "Rules:\n"
    "- Natural, friendly, concise wording, as a native app would use.\n"
    "- Short texts are button labels: keep them short.\n"
    "- Keep placeholders such as %(name)s exactly as they are.\n"
    "- Keep emoji, numbers, and the names AIstars, Telegram and Telegram Stars.\n"
    "- Keep the names of AI models and companies (GPT, Claude, OpenAI, Anthropic...) "
    "and technical values such as 1024x1024 or 16:9 as they are.\n"
    "- No HTML, no Markdown, no quotes around the text.\n"
    "- Answer with one JSON object only: the same keys, each with its translation."
)


class TranslationError(Exception):
    pass


def model_for(language, model=None):
    """The model to use: the one asked for, the language's own, or the default."""
    from apps.ai.models import AIModel

    chosen = model or language.translator or AIModel.translation_default()
    if chosen is None:
        raise TranslationError("no translation model: mark one as the default in AI → Models")
    if not chosen.usable:
        raise TranslationError(f"{chosen} cannot be used: add {chosen.provider}'s API key "
                               "in AI → Providers, and check the model is enabled")
    return chosen


def pending(language, overwrite=False, overwrite_manual=False):
    """The active texts this run should translate."""
    sources = SourceText.objects.filter(active=True)
    done = Translation.objects.filter(language=language)
    if not overwrite:
        return list(sources.exclude(translations__language=language))
    if not overwrite_manual:
        return list(sources.exclude(pk__in=done.filter(manual=True).values("source_id")))
    return list(sources)


def check(source: str, text: str) -> str:
    """'' if `text` is a usable translation of `source`, else the reason."""
    if not text or not text.strip():
        return "empty"
    if sorted(PLACEHOLDER.findall(text)) != sorted(PLACEHOLDER.findall(source)):
        return "placeholders changed"
    if TAG.search(text):
        return "contains HTML"
    if len(text) > 4 * len(source) + 40:
        return "far too long"
    return ""


def parse_reply(text: str) -> dict:
    """The JSON object in a model's answer, tolerating a code fence around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise TranslationError("the model did not answer with JSON")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise TranslationError(f"the model's JSON is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise TranslationError("the model's JSON is not an object")
    return data


def translate_batch(language, model, sources) -> tuple:
    """Translate up to BATCH texts; returns (saved, rejected reasons)."""
    from apps.ai import clients

    numbered = {str(index): source.text for index, source in enumerate(sources, 1)}
    system = SYSTEM.format(native=language.native_name, name=language.name,
                           code=language.code)
    reply = clients.ask(model, system, json.dumps(numbered, ensure_ascii=False),
                        purpose="translation")
    answers = parse_reply(reply.text)

    saved, rejected = 0, []
    for key, source in zip(numbered, sources):
        text = str(answers.get(key, "")).strip()
        reason = check(source.text, text)
        if reason:
            rejected.append(f"“{source.text[:30]}”: {reason}")
            continue
        Translation.objects.update_or_create(
            language=language, source=source,
            defaults={"text": text, "model": model.model_id, "manual": False})
        saved += 1
    return saved, rejected


def run(code, model_id=None, overwrite=False, overwrite_manual=False):
    """
    Translate one language. Always leaves a status the admin can read:
    done, or failed with the reason.
    """
    from apps.ai.models import AIModel

    language = Language.objects.get(code=code)
    if code == i18n.SOURCE:
        return _finish(language, TranslationStatus.FAILED,
                       "English is the source text, written in the code")
    try:
        model = model_for(language, AIModel.objects.filter(pk=model_id).first()
                          if model_id else None)
    except TranslationError as exc:
        return _finish(language, TranslationStatus.FAILED, str(exc))

    sources = pending(language, overwrite, overwrite_manual)
    total, saved, rejected = len(sources), 0, []
    _status(language, TranslationStatus.RUNNING, f"0 of {total} with {model}")
    for start in range(0, total, BATCH):
        try:
            done, refused = translate_batch(language, model, sources[start:start + BATCH])
        except Exception as exc:  # AI, JSON or budget: stop, keep what is saved
            i18n.changed()
            return _finish(language, TranslationStatus.FAILED,
                           f"{saved} of {total} done, then: {exc}")
        saved += done
        rejected += refused
        i18n.changed()
        _status(language, TranslationStatus.RUNNING, f"{saved} of {total} with {model}")

    detail = f"{saved} of {total} translated with {model}"
    if rejected:
        detail += f"; kept in English: {'; '.join(rejected)}"
    return _finish(language, TranslationStatus.DONE, detail[:255])


def _status(language, status, detail):
    Language.objects.filter(pk=language.pk).update(status=status, status_detail=detail[:255])


def _finish(language, status, detail):
    fields = {"status": status, "status_detail": detail[:255]}
    if status == TranslationStatus.DONE:
        fields["translated_at"] = timezone.now()
    Language.objects.filter(pk=language.pk).update(**fields)
    log = activity.info if status == TranslationStatus.DONE else activity.warn
    log(LogCategory.TRANSLATION, f"translation_{status}", f"{language.name}: {detail}",
        detail={"language": language.code})
    return status


def queue(code, model_id=None, overwrite=False, overwrite_manual=False):
    """Start a background translation, after the current transaction commits."""
    from .tasks import translate_language

    Language.objects.filter(code=code).update(status=TranslationStatus.QUEUED,
                                              status_detail="")
    transaction.on_commit(lambda: translate_language.delay(
        code, model_id=model_id, overwrite=overwrite, overwrite_manual=overwrite_manual))


def missing_count(language) -> int:
    return SourceText.objects.filter(active=True).exclude(
        translations__language=language).count()
