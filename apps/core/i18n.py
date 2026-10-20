"""
The bot's texts and their translations.

English is the source, written in the code as `_("...")` (menus.py). Every
deploy reads the code and records each text (sync_sources); the AI translates
them per language into the database (translation.py). `gettext` looks a text
up in the active language (languages.activate_for) and falls back to English.
Texts that live in the database (the model catalog's descriptions, written
for users) are recorded the same way by record_catalog.

Rules for texts in the code: plain text, no HTML (the code adds formatting
around it); named placeholders such as %(name)s, never %s; and no number
agreement ("Credits: 5", not "5 credits"), because plural forms differ per
language.
"""

import ast
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.utils import translation

SOURCE = "en"

# Calls whose first argument is a text to translate.
KEYWORDS = {"_", "gettext"}

# Where the bot's texts live; tests and migrations hold none.
SKIP_DIRS = {"tests", "migrations", "__pycache__"}

VERSION_KEY = "i18n:version"
LOCAL_TTL = 10   # seconds a process keeps a catalog before checking for edits

_local = {}      # code -> (expires, version, {english: translation})


# --------------------------------------------------------------------------
# Looking texts up
# --------------------------------------------------------------------------

def gettext(text: str) -> str:
    """`text` in the active language, or as written if it has no translation."""
    code = translation.get_language() or SOURCE
    if code == SOURCE:
        return text
    return catalog(code).get(text) or text


_ = gettext


def catalog(code) -> dict:
    """{english: translation} for one language, shared by every process."""
    if not getattr(settings, "I18N_CACHE", True):
        return _load(code)
    now = time.monotonic()
    cached = _local.get(code)
    if cached and cached[0] > now:
        return cached[2]

    version = cache.get_or_set(VERSION_KEY, 1, None)
    if cached and cached[1] == version:
        _local[code] = (now + LOCAL_TTL, version, cached[2])
        return cached[2]

    texts = _load(code)
    _local[code] = (now + LOCAL_TTL, version, texts)
    return texts


def _load(code) -> dict:
    from .models import Translation

    return dict(Translation.objects.filter(language_id=code, source__active=True)
                .values_list("source__text", "text"))


def changed():
    """Call after any translation is written: every process reloads."""
    try:
        cache.incr(VERSION_KEY)
    except ValueError:   # the key expired or was never set
        cache.set(VERSION_KEY, 2, None)
    _local.clear()


# --------------------------------------------------------------------------
# Finding the texts in the code
# --------------------------------------------------------------------------

def extract(root=None) -> dict:
    """{text: 'file:line'} for every `_("...")` under apps/, in code order."""
    root = Path(root or settings.BASE_DIR)
    found = {}
    for path in sorted((root / "apps").rglob("*.py")):
        if SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            text = _text_of(node)
            if text and text not in found:
                found[text] = f"{path.relative_to(root)}:{node.lineno}"
    return dict(sorted(found.items(), key=lambda item: item[1]))


def _text_of(node):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in KEYWORDS and node.args):
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def sync_sources(root=None) -> list:
    """
    Record the texts now in the code; mark the others inactive. Returns the
    texts that are new, so their translations can be started.
    """
    from .models import SourceText

    found = extract(root)
    new = []
    for text, location in found.items():
        source, created = SourceText.objects.get_or_create(
            text=text, defaults={"location": location})
        if created:
            new.append(source)
        elif not source.active or source.location != location:
            SourceText.objects.filter(pk=source.pk).update(active=True, location=location)
    SourceText.objects.filter(active=True).exclude(text__in=found).exclude(
        location__startswith=CATALOG).update(active=False)
    changed()
    return new


CATALOG = "catalog:"


def record_catalog(texts: dict) -> list:
    """
    Texts from the database rather than the code (the model catalog's
    descriptions): text -> location, which starts with "catalog:". The
    catalog texts not given are marked inactive. Returns the new ones.
    """
    from .models import SourceText

    new = []
    for text, location in texts.items():
        source, created = SourceText.objects.get_or_create(
            text=text, defaults={"location": location})
        if created:
            new.append(source)
        elif not source.active and source.location.startswith(CATALOG):
            SourceText.objects.filter(pk=source.pk).update(active=True, location=location)
    SourceText.objects.filter(active=True, location__startswith=CATALOG).exclude(
        text__in=list(texts)).update(active=False)
    changed()
    return new
