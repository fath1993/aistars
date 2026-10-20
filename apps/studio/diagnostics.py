"""The doctor's AI tools section: who may use them, what is on offer, what is stuck."""

from datetime import timedelta

from django.utils import timezone

from apps.ai.models import AIModel, Use
from apps.core import runtime
from apps.core.diagnostics import OK, WARN
from apps.core.models import Account, ControlPanel

from .models import ACTIVE, Assistant, Job

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

    for use, name in Use.choices:
        offered = AIModel.offered_for(use, panel.demo_mode)
        if not offered:
            report.add(WARN, name, "no model users can have: this tool does not show",
                       FIX, section)
            continue
        default = AIModel.default_for(use, panel.demo_mode)
        report.add(OK, name, ", ".join(
            f"{model}{' (default)' if model == default else ''}" for model in offered), "",
            section)
    unlisted = AIModel.objects.filter(offered=True, listed=False).count()
    if unlisted:
        report.add(WARN, "Models not at the provider",
                   f"{unlisted} offered model(s) the provider does not list",
                   "AI → Models, filter Listed: No. Check their IDs, or stop offering them.",
                   section)

    assistants = Assistant.objects.filter(active=True).count()
    report.add(OK if assistants else WARN, "Assistants", f"{assistants} on offer",
               "" if assistants else "AI tools → Assistants: switch one on.", section)

    archive = runtime.get("ARCHIVE_CHAT_ID")
    if archive:
        report.add(OK, "Archive channel", str(archive), "", section)
    else:
        report.add(WARN, "Archive channel", "not set: photos, images and voice notes are not "
                   "archived", "Set Your Telegram ID, then make the bot an admin of a private "
                   "channel from your own account; it is recorded by itself.", section)

    stuck = Job.objects.filter(status__in=ACTIVE,
                               created_at__lt=timezone.now() - timedelta(minutes=10)).count()
    if stuck:
        report.add(WARN, "Requests being made", f"{stuck} older than 10 minutes",
                   "The sweep returns their credits after 20 minutes; check the celery "
                   "container if this stays.", section)
    else:
        report.add(OK, "Requests being made", "none stuck", "", section)
