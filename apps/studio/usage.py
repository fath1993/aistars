"""
What a user has spent, for the 📊 Usage page: today and the last 7 and 30
days, by tool, agent and model; their last requests; and the daily limit
they may set themselves (checked in jobs.start).
"""

from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from apps.ai.models import AIModel
from apps.core.i18n import gettext as _

from .models import Assistant, Job, Kind, Profile, Status

TOOLS = {Kind.CHAT: "chat", Kind.IMAGE: "images", Kind.EDIT: "images",
         Kind.TRANSCRIBE: "voice", Kind.SPEECH: "voice"}
TOP = 5
PER_PAGE = 8


def day_start(days_back=0):
    """Midnight, `days_back` days ago (local time)."""
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days_back)


def _done(account, since):
    return Job.objects.filter(account=account, status=Status.DONE,
                              created_at__gte=since).order_by()


def periods(account) -> list:
    """[(label, credits, requests)] for today, 7 days and 30 days."""
    rows = []
    for label, since in ((_("Today"), day_start()), (_("7 days"), day_start(6)),
                         (_("30 days"), day_start(29))):
        total = _done(account, since).aggregate(credits=Sum("charged"), requests=Count("id"))
        rows.append((label, total["credits"] or 0, total["requests"]))
    return rows


def _grouped(account, field, **only) -> dict:
    return {key: credits for key, credits in _done(account, day_start(29)).filter(**only)
            .values(field).annotate(credits=Sum("charged")).values_list(field, "credits")
            if credits}


def by_tool(account) -> list:
    """[(tool, credits)], most first, over 30 days."""
    totals = {}
    for kind, credits in _grouped(account, "kind").items():
        tool = TOOLS.get(kind, kind)
        totals[tool] = totals.get(tool, 0) + credits
    return sorted(totals.items(), key=lambda row: -row[1])


def by_agent(account) -> list:
    """[(agent title, credits)] for chat answers, the General assistant included."""
    from .texts import agent_title

    rows = _grouped(account, "assistant", kind=Kind.CHAT)
    if not any(rows):
        return []   # no agent used: the tool line says it all
    agents = {agent.pk: agent for agent in Assistant.objects.filter(pk__in=[k for k in rows if k])}
    named = [(agent_title(agents[key]) if key in agents else "🤖 " + _("General assistant"),
              credits) for key, credits in rows.items()]
    return sorted(named, key=lambda row: -row[1])[:TOP]


def by_model(account) -> list:
    rows = _grouped(account, "model")
    models = {model.pk: model for model in AIModel.objects.filter(pk__in=rows)}
    return sorted(((str(models[key]), credits) for key, credits in rows.items() if key in models),
                  key=lambda row: -row[1])[:TOP]


def last(account, page=0) -> tuple:
    """(the user's requests, newest first, one page; has_more)."""
    rows = list(Job.objects.filter(account=account).select_related("model", "assistant")
                .order_by("-id")[page * PER_PAGE:(page + 1) * PER_PAGE + 1])
    return rows[:PER_PAGE], len(rows) > PER_PAGE


def set_limit(account, amount):
    """The most the user's requests may cost a day; 0 for none."""
    from .chat import profile

    Profile.objects.filter(pk=profile(account).pk).update(daily_limit=amount or None)

