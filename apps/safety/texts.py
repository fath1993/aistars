"""What the user is told about a block: the category only, never how it was found."""

from apps.core.i18n import gettext as _

from .models import Category


def category_text(category) -> str:
    return {
        Category.SEXUAL: _("sexual content"),
        Category.MINORS: _("minors"),
        Category.VIOLENCE: _("violence or gore"),
        Category.HATE: _("hate or harassment"),
        Category.SELF_HARM: _("self-harm"),
        Category.REAL_PERSON: _("a real person"),
        Category.BRAND: _("brands or logos"),
        Category.COPYRIGHT: _("copyrighted characters"),
        Category.ILLEGAL: _("illegal activity"),
    }.get(category, _("content we do not allow"))
