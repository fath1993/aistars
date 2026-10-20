"""
Which language the bot speaks to each user.

Every language of the world is a Language row; the admin ticks which ones
users may choose (`used`). English is the source text: it needs no
translation, and anything untranslated shows in English.

A user's language, first match wins, used languages only:

    1. the one they chose in the bot (Account.language)
    2. their country's default language (Country.default_language)
    3. their Telegram app's language
    4. English
"""

from django.utils import translation

from .i18n import SOURCE


def used_codes() -> list:
    """Offered to users, in admin order."""
    from .models import Language

    return list(Language.objects.filter(used=True).values_list("code", flat=True))


def name(code) -> str:
    """The language's own name: English, Română, Русский."""
    from .models import Language

    return (Language.objects.filter(code=code).values_list("native_name", flat=True)
            .first() or code)


def names(codes) -> dict:
    """{code: own name} in one query."""
    from .models import Language

    found = dict(Language.objects.filter(code__in=codes).values_list("code", "native_name"))
    return {code: found.get(code, code) for code in codes}


def _telegram_candidates(language_code):
    """Telegram sends IETF tags: 'pt-BR' -> ['pt-br', 'pt']."""
    if not language_code:
        return []
    full = language_code.lower()
    primary = full.split("-")[0]
    return [full] if full == primary else [full, primary]


def resolve(account, used=None) -> str:
    """The code of the language to speak to this account (`used`: preloaded)."""
    used = used_codes() if used is None else used
    candidates = [account.language]
    if account.country_id and account.country.default_language_id:
        candidates.append(account.country.default_language_id)
    candidates += _telegram_candidates(account.language_code)
    for code in candidates:
        if code and code in used:
            return code
    return SOURCE


def activate_for(account):
    """`with languages.activate_for(account):` -- every text inside speaks it."""
    return translation.override(resolve(account))
