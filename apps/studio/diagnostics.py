"""The doctor's AI tools section: who may use them, what is on offer, what is stuck."""

from datetime import timedelta

from django.utils import timezone

from apps.ai.models import AIModel, Provider, Use
from apps.core import runtime
from apps.core.diagnostics import OK, WARN
from apps.core.models import Account, ControlPanel

from .models import ACTIVE, Assistant, Category, Job

FIX = ("AI → Providers → OpenAI: paste the API key. Then AI → Models: offer a model of this "
       "use, with its prices.")


def check(report, section):
    panel = ControlPanel.get()
    testers = Account.objects.filter(tester=True).count()
    label = "Demo mode (no provider is called)" if panel.demo_mode else "Live, with the providers"
    if not panel.demo_mode and panel.open_to_everyone:
        report.add(OK, "Using the tools", f"{label}, open to everyone", "", section)
    elif testers:
        report.add(OK, "Using the tools", f"{label}, for {testers} tester(s) only", "", section)
    else:
        report.add(WARN, "Using the tools", f"{label}, but no account is a tester: nobody can "
                   "use them", "Accounts → select yours → action Tester.", section)

    names = dict(Use.choices)
    providers = Provider.for_users(panel.demo_mode)
    if not providers:
        report.add(WARN, "AI providers", "none offers users anything: the tools do not show",
                   FIX, section)
    for provider in providers:
        parts = []
        for use in provider.offers(panel.demo_mode):
            default = AIModel.default_for(use, panel.demo_mode, provider)
            parts.append(f"{names[use]}: " + ", ".join(
                f"{model}{' (default)' if model == default else ''}"
                for model in AIModel.offered_for(use, panel.demo_mode, provider)))
        report.add(OK, f"Provider {provider}", " · ".join(parts), "", section)
    default = str(runtime.get("DEFAULT_PROVIDER") or "")
    if providers and default not in {provider.kind for provider in providers}:
        report.add(WARN, "Default AI provider", f"{default or 'not set'} offers users nothing: "
                   f"they get {providers[0]}", "Runtime settings → Default AI provider, or "
                   "give that provider a key and offered models.", section)
    elif providers:
        report.add(OK, "Default AI provider", default, "", section)
    unlisted = AIModel.objects.filter(offered=True, listed=False).count()
    if unlisted:
        report.add(WARN, "Models not at the provider",
                   f"{unlisted} offered model(s) the provider does not list",
                   "AI → Models, filter Listed: No. Check their IDs, or stop offering them.",
                   section)

    assistants = Assistant.objects.filter(active=True).count()
    categories = Category.objects.filter(active=True).count()
    report.add(OK if assistants else WARN, "Agents",
               f"{assistants} on offer in {categories} categories",
               "" if assistants else "AI tools → Agents: switch one on.", section)
    homeless = Assistant.objects.filter(active=True).exclude(category__active=True).count()
    if homeless:
        report.add(WARN, "Agents without a category",
                   f"{homeless} agent(s) on offer are in no category users can open: they "
                   "show in search and links only", "AI tools → Agents: give them a category.",
                   section)

    archive = runtime.get("ARCHIVE_CHAT_ID")
    if archive:
        report.add(OK, "Archive channel", str(archive), "", section)
    else:
        report.add(WARN, "Archive channel", "not set: photos, images and voice notes are not "
                   "archived", "Control panel → Archive channel: make the bot an admin of a "
                   "private channel, post anything there, then pick it.", section)

    writer = AIModel.translation_default()
    if panel.demo_mode or (writer is not None and writer.usable):
        report.add(OK, "Chat memory", f"{runtime.get('CHAT_MEMORY_TOKENS')} tokens word for "
                   f"word, older messages summarized by {writer or 'Demo mode'}", "", section)
    else:
        report.add(WARN, "Chat memory", "no usable model marked Default for translation: long "
                   "chats are not summarized, their oldest messages are just left out",
                   "AI → Models: tick Default for translation on a cheap chat model with a key.",
                   section)

    stuck = Job.objects.filter(status__in=ACTIVE,
                               created_at__lt=timezone.now() - timedelta(minutes=10)).count()
    if stuck:
        report.add(WARN, "Requests being made", f"{stuck} older than 10 minutes",
                   "The sweep returns their credits after 20 minutes; check the celery "
                   "container if this stays.", section)
    else:
        report.add(OK, "Requests being made", "none stuck", "", section)
