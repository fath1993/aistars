"""
System checks, as data.

One implementation, two front ends: the admin page renders these with
buttons, and `manage.py doctor` prints them. A diagnosis you can only get over
SSH is not much of a diagnosis.
"""

import datetime
from dataclasses import dataclass, field

from django.db import connection
from django.utils import timezone

from . import runtime

# A delivery error older than this, with nothing queued behind it, is treated
# as historical rather than current.
STALE_ERROR_MINUTES = 15

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class Check:
    status: str
    label: str
    detail: str = ""
    fix: str = ""
    section: str = ""


@dataclass
class Report:
    checks: list = field(default_factory=list)

    def add(self, status, label, detail="", fix="", section=""):
        self.checks.append(Check(status, label, detail, fix, section))

    @property
    def failures(self):
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self):
        return [c for c in self.checks if c.status == WARN]

    @property
    def healthy(self):
        return not self.failures

    def sections(self):
        """Insertion order, so the output reads as a path."""
        grouped = {}
        for check in self.checks:
            grouped.setdefault(check.section, []).append(check)
        return grouped


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def register_webhook(url=""):
    """
    Point Telegram at this app. Returns (ok, message).

    The URL is built from PUBLIC_BASE_URL rather than typed: a hand-typed
    webhook URL is how updates end up delivered nowhere for weeks.
    """
    from .services import telegram

    target = url or runtime.webhook_url()
    if not target:
        return False, ("PUBLIC_BASE_URL is not set, so the webhook URL cannot "
                       "be built. Set it in Runtime settings first.")
    if not runtime.get("TELEGRAM_BOT_TOKEN"):
        return False, "The bot token is not set."
    if not runtime.get("TELEGRAM_WEBHOOK_SECRET"):
        return False, ("The webhook secret is not set. Registering without one "
                       "would make the endpoint reject every update.")

    if telegram.set_webhook(target):
        from . import activity
        from .models import LogCategory

        activity.info(LogCategory.SYSTEM, "webhook_registered", target)
        return True, f"Webhook registered: {target}."
    return False, ("Telegram refused the registration. Check the bot token, "
                   "and that the domain has a valid certificate.")


def resume_platform():
    from . import activity
    from .models import LogCategory
    from .services import budget

    budget.resume()
    activity.info(LogCategory.ADMIN, "resumed", "kill switch cleared")
    return True, "Resumed. Paid requests are allowed again."


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def run(include_telegram=True) -> Report:
    report = Report()
    token = _configuration(report)
    if include_telegram:
        _telegram(report, token)
    _application(report)
    _languages_and_regions(report)
    _ai(report)
    _billing(report)
    _studio(report)
    _safety(report)
    return report


def _configuration(report):
    section = "Configuration"
    token = runtime.get("TELEGRAM_BOT_TOKEN")
    if not token:
        report.add(FAIL, "Bot token", "not set",
                   "Set TELEGRAM_BOT_TOKEN in Runtime settings.", section)
    else:
        problem = runtime.validate("TELEGRAM_BOT_TOKEN", str(token))
        if problem:
            report.add(FAIL, "Bot token", problem,
                       "A leading 'bot' is the usual cause; it is stripped "
                       "automatically when you save.", section)
        else:
            report.add(OK, "Bot token", f"{'•' * 6}{str(token)[-4:]}", "", section)

    if not runtime.get("TELEGRAM_WEBHOOK_SECRET"):
        report.add(FAIL, "Webhook secret", "not set",
                   "An empty secret rejects every update with 403. Set it, "
                   "then register the webhook.", section)
    else:
        report.add(OK, "Webhook secret", "set", "", section)

    base = runtime.get("PUBLIC_BASE_URL")
    if not base:
        report.add(FAIL, "Public base URL", "not set",
                   "Set PUBLIC_BASE_URL (scheme and host only). The webhook "
                   "URL is built from it.", section)
    else:
        problem = runtime.validate("PUBLIC_BASE_URL", str(base))
        if problem:
            report.add(FAIL, "Public base URL", problem, "Correct it.", section)
        else:
            report.add(OK, "Public base URL",
                       f"{base} → {runtime.webhook_url()}", "", section)
    return token


def _telegram(report, token):
    section = "Telegram"
    if not token:
        report.add(WARN, "Telegram API", "skipped, no token", "", section)
        return

    from .services import telegram

    me = telegram._call("getMe")
    if not me:
        report.add(FAIL, "Bot identity", "no response from Telegram",
                   "The token is set but was rejected, or the container has no "
                   "outbound network.", section)
        return
    report.add(OK, "Bot identity", f"@{me.get('username')}", "", section)

    info = telegram._call("getWebhookInfo")
    if not info:
        report.add(FAIL, "Webhook info", "no response", "Check outbound network.",
                   section)
        return

    url = info.get("url") or ""
    expected = runtime.webhook_url()
    if not url:
        report.add(FAIL, "Webhook URL", "not registered",
                   "Telegram is holding your updates. Use the Register webhook "
                   "button.", section)
    elif expected and url == expected:
        report.add(OK, "Webhook URL", url, "", section)
    else:
        report.add(FAIL, "Webhook URL",
                   f"{url}   (expected {expected or 'set PUBLIC_BASE_URL first'})",
                   "Use the Register webhook button.", section)

    allowed = info.get("allowed_updates")
    if allowed is not None and "pre_checkout_query" not in allowed:
        report.add(WARN, "Payment updates", "Telegram does not send the payment checks",
                   "Press Register the Telegram webhook: until then every Stars payment "
                   "is cancelled after 10 seconds.", section)
    if allowed is not None and "channel_post" not in allowed:
        report.add(WARN, "Channel posts", "Telegram does not send posts in channels",
                   "Press Register the Telegram webhook: until then the Archive channel "
                   "page cannot list your channel.", section)
    if allowed is not None and "my_chat_member" not in allowed:
        report.add(WARN, "Channel updates", "Telegram does not say when the bot is made a "
                   "channel admin", "Press Register the Telegram webhook: until then the "
                   "archive channel cannot be set.", section)

    pending = info.get("pending_update_count", 0)
    report.add(WARN if pending else OK, "Pending updates", str(pending),
               "Telegram has updates it could not deliver; the error below "
               "usually explains why." if pending else "", section)

    error = info.get("last_error_message")
    if not error:
        report.add(OK, "Last delivery error", "none", "", section)
        return

    from django.core.cache import cache

    from .views import LAST_DELIVERY_KEY

    when = info.get("last_error_date")
    stamp = (datetime.datetime.fromtimestamp(when).strftime("%d %b %H:%M")
             if when else "?")

    # getWebhookInfo never clears last_error, so a 502 during a restart would
    # stay red for ever without this.
    last_ok = cache.get(LAST_DELIVERY_KEY)
    age_minutes = ((timezone.now().timestamp() - when) / 60) if when else 0

    if pending == 0 and when and last_ok and last_ok > when:
        report.add(OK, "Last delivery error",
                   f"{stamp}: {error} — recovered, an update has been "
                   f"delivered since", "", section)
        return

    if pending == 0 and age_minutes > STALE_ERROR_MINUTES:
        report.add(WARN, "Last delivery error", f"{stamp}: {error}",
                   "Nothing is queued, so this is most likely a restart during "
                   "a deploy. Message the bot and re-run to confirm.", section)
        return

    hint = {
        "403": "The secret does not match. Register the webhook again.",
        "404": "nginx is not routing /bot/ to the app.",
        "502": "nginx cannot reach the container.",
        "504": "The app did not answer in time.",
        "SSL": "Telegram requires a valid certificate.",
    }
    advice = next((v for k, v in hint.items() if k in error), "")
    report.add(FAIL, "Last delivery error", f"{stamp}: {error}", advice, section)


def _application(report):
    section = "Application"
    from .models import Account, AccountStatus, ControlPanel

    try:
        connection.ensure_connection()
        report.add(OK, "Database", "reachable", "", section)
    except Exception as exc:
        report.add(FAIL, "Database", str(exc)[:80], "Check the database container.",
                   section)
        return

    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if plan:
        report.add(FAIL, "Migrations", f"{len(plan)} unapplied",
                   "The entrypoint runs migrate on boot; restart the app "
                   "container.", section)
    else:
        report.add(OK, "Migrations", "up to date", "", section)

    panel = ControlPanel.get()
    if panel.paused:
        report.add(FAIL, "Kill switch",
                   f"PAUSED — {panel.pause_reason or 'no reason recorded'}",
                   "Use the Resume button.", section)
    else:
        report.add(OK, "Kill switch", "running", "", section)

    active = Account.objects.filter(status=AccountStatus.ACTIVE).count()
    blocked = Account.objects.filter(status=AccountStatus.BLOCKED).count()
    report.add(OK, "Accounts", f"{active} active, {blocked} blocked", "", section)


def _languages_and_regions(report):
    section = "Languages and regions"
    from . import languages, translation
    from .models import Country, Language, TranslationStatus

    try:
        used = languages.used_codes()
    except Exception as exc:  # no database: the Application section says so
        report.add(FAIL, "Languages", str(exc)[:80], "", section)
        return

    if used:
        names = languages.names(used)
        report.add(OK, "Languages offered", ", ".join(names[code] for code in used), "",
                   section)
    else:
        report.add(WARN, "Languages offered", "none: everyone gets English",
                   "Tick Used for at least one language in Languages.", section)

    for language in Language.objects.filter(translate=True).exclude(code="en"):
        missing = translation.missing_count(language)
        label = f"Translation: {language.name}"
        if language.status == TranslationStatus.FAILED:
            report.add(WARN, label, language.status_detail or "failed",
                       "Fix the cause, then Languages → Translate missing texts now.",
                       section)
        elif missing:
            report.add(WARN, label, f"{missing} text(s) still in English "
                       f"({language.get_status_display().lower()})",
                       "They are translated in the background within 30 minutes.", section)
        else:
            report.add(OK, label, "complete", "", section)

    allowed = Country.objects.filter(allowed=True).count()
    if allowed:
        report.add(OK, "Allowed countries", f"{allowed} of {Country.objects.count()}",
                   "", section)
    else:
        report.add(WARN, "Allowed countries", "none: nobody can register",
                   "Allow at least one country in Countries.", section)


def _ai(report):
    section = "AI"
    from apps.ai.models import AIModel

    try:
        model = AIModel.translation_default()
    except Exception as exc:
        report.add(FAIL, "Translation model", str(exc)[:80], "", section)
        return
    if model is None:
        report.add(WARN, "Translation model", "none marked as default",
                   "Tick Default for translation on one model in AI → Models.", section)
    elif not model.usable:
        report.add(WARN, "Translation model", f"{model}: no API key or switched off",
                   f"Add {model.provider}'s API key in AI → Providers.", section)
    else:
        report.add(OK, "Translation model", f"{model} ({model.provider})", "", section)


def _billing(report):
    section = "Billing"
    from apps.billing import pricing
    from apps.billing.models import CreditRequest, Pack, RequestStatus

    try:
        packs = Pack.objects.filter(active=True).count()
    except Exception as exc:
        report.add(FAIL, "Packs", str(exc)[:80], "", section)
        return
    if packs:
        report.add(OK, "Packs on sale", str(packs), "", section)
    else:
        report.add(WARN, "Packs on sale", "none: nobody can buy credits",
                   "Switch on a pack in Billing → Packs.", section)

    if runtime.get("SUPPORT_CONTACT"):
        report.add(OK, "Support contact", str(runtime.get("SUPPORT_CONTACT")), "", section)
    else:
        report.add(WARN, "Support contact", "not set",
                   "Runtime settings → Support contact. Telegram requires bots that take "
                   "Stars to answer /paysupport.", section)

    report.add(OK, "Pricing",
               f"{pricing.margin_percent()}% margin, one Star = ${pricing.star_value_usd()}: "
               f"a $1.00 cost is 🪙 {pricing.credits_for(1)}", "", section)

    waiting = CreditRequest.objects.filter(status=RequestStatus.PENDING).count()
    if waiting:
        report.add(WARN, "Credit requests", f"{waiting} waiting for a decision",
                   "Billing → Credit requests.", section)
    else:
        report.add(OK, "Credit requests", "none waiting", "", section)


def _studio(report):
    """The AI tools: who may use them, the models on offer, the archive, stuck jobs."""
    section = "AI tools"
    from apps.studio import diagnostics as studio

    try:
        studio.check(report, section)
    except Exception as exc:  # no database: the Application section says so
        report.add(FAIL, "AI tools", str(exc)[:80], "", section)


def _safety(report):
    section = "Safety"
    from apps.ai.models import AIModel, Provider, ProviderKind
    from apps.core.models import ControlPanel
    from apps.safety.models import BlockedTerm, Policy, Result, Review, Screening

    try:
        words = BlockedTerm.objects.filter(active=True).count()
    except Exception as exc:
        report.add(FAIL, "Blocklist", str(exc)[:80], "", section)
        return
    report.add(OK if words else WARN, "Blocklist", f"{words} word(s)",
               "" if words else "Safety → Blocked words: add the plainest ones.", section)

    openai = Provider.objects.filter(kind=ProviderKind.OPENAI).first()
    if openai is not None and openai.usable:
        report.add(OK, "OpenAI Moderation", "on", "", section)
    else:
        report.add(WARN, "OpenAI Moderation", "off: no OpenAI key, only the blocklist reads "
                   "chat messages", "AI → Providers → OpenAI: add the key (Moderation is "
                   "free).", section)

    judge = AIModel.safety_default()
    live = not ControlPanel.get().demo_mode
    if judge is not None and judge.usable:
        report.add(OK, "AI policy judge", f"{judge} ({judge.provider})", "", section)
    else:
        why = ("no model marked Default for safety" if judge is None
               else f"{judge}: no API key or switched off")
        effect = ("every image request is refused" if live
                  else "skipped in Demo mode, required outside it")
        report.add(WARN, "AI policy judge", f"{why}: {effect}",
                   "AI → Models: tick Default for safety on a model that sees images, and "
                   "add its provider's key in AI → Providers.", section)

    report.add(OK, "Policy", f"{len(Policy.get().text)} characters, edited "
               f"{timezone.localtime(Policy.get().updated_at):%Y-%m-%d}", "", section)

    waiting = Screening.objects.filter(result=Result.BLOCKED, review=Review.OPEN).count()
    if waiting:
        report.add(WARN, "Review queue", f"{waiting} blocked request(s) to review",
                   "Safety → Review queue, filter Waiting for review: Confirm, or take the "
                   "strike back.", section)
    else:
        report.add(OK, "Review queue", "nothing waiting", "", section)

    report.add(OK, "Strikes before blocking", str(runtime.get("SAFETY_STRIKE_LIMIT")), "",
               section)
