"""
What a user is offered: the functions of the AI provider they chose (Profile
→ 🧠 AI provider), or of the default one (Runtime settings → Default AI
provider). The bot is built from this: a tool their provider does not offer
has no button, no command and no screen, and anything sent for it gets one
line saying so. Nothing is ever done by another provider instead.

    CHAT            chats and agents (photos and files the model reads)
    IMAGE           create an image; edit a photo when a model can
    TRANSCRIPTION   voice to text; voice notes in a chat
    SPEECH          text to speech; 🔊 Listen under answers
"""

from apps.ai.models import AIModel, Provider, Use
from apps.core import runtime
from apps.core.i18n import gettext as _

from . import jobs

CHAT, IMAGE, TRANSCRIPTION, SPEECH = Use.CHAT, Use.IMAGE, Use.TRANSCRIPTION, Use.SPEECH
# The 🎙 Voice screen: either voice function.
VOICE = "voice"


def providers() -> list:
    """The providers users can choose: those that offer anything right now."""
    return Provider.for_users(jobs.demo())


def provider_of(account):
    """The user's provider if it offers anything, else the default, else the first; or None."""
    from .chat import profile

    available = providers()
    chosen = profile(account).provider_id
    default = str(runtime.get("DEFAULT_PROVIDER") or "")
    return (next((provider for provider in available if provider.pk == chosen), None)
            or next((provider for provider in available if provider.kind == default), None)
            or (available[0] if available else None))


def functions(account) -> list:
    """What the user is offered (Use values); empty when no provider offers anything."""
    provider = provider_of(account)
    return provider.offers(jobs.demo()) if provider is not None else []


def has(account, function) -> bool:
    """`function` is offered (VOICE: either voice function)."""
    return _met(function, functions(account))


def _met(function, offered) -> bool:
    if function == VOICE:
        return TRANSCRIPTION in offered or SPEECH in offered
    return function in offered


def model(account, use):
    """The default model of the user's provider for `use`, or None when it has none."""
    provider = provider_of(account)
    return AIModel.default_for(use, jobs.demo(), provider) if provider is not None else None


def models(account, use) -> list:
    """The models of the user's provider users can choose for `use`."""
    provider = provider_of(account)
    return AIModel.offered_for(use, jobs.demo(), provider) if provider is not None else []


def mine(account, candidate) -> bool:
    """`candidate` is a model of the user's provider that users can have now."""
    provider = provider_of(account)
    return (candidate is not None and provider is not None
            and candidate.provider_id == provider.pk and candidate.ready_for(jobs.demo()))


def choose(account, provider):
    """For everything from now on; what was being asked for is dropped."""
    from . import state
    from .chat import profile
    from .models import Profile

    Profile.objects.filter(pk=profile(account).pk).update(provider=provider)
    state.stop(account)


def function_name(function) -> str:
    return {CHAT: _("Chat"), IMAGE: _("Images"), TRANSCRIPTION: _("Voice to text"),
            SPEECH: _("Text to speech"), VOICE: _("Voice")}.get(function, function)


def not_offered_text(account, function) -> str:
    """One line for a tool the user's provider does not offer."""
    provider = provider_of(account)
    if provider is None:
        return _("No AI model is on offer for this right now. Please try again later.")
    text = _("Not offered with %(provider)s: %(tool)s.") % {
        "provider": provider, "tool": function_name(function)}
    elsewhere = any(_met(function, other.offers(jobs.demo()))
                    for other in providers() if other.pk != provider.pk)
    if elsewhere:
        text += " " + _("You can change the provider in 👤 Profile → 🧠 AI provider.")
    return text
