"""
A request's life: held, queued, made in the background, then charged what it
really cost (or returned whole when it fails).

    start()   price the most it can cost, hold that, record the Job
    run()     the Celery task: make it (chat.answer, images.make, voice.*)
    done()    charge what it really cost; never more than the hold
    failed()  give the hold back, and say so in the log

A sweep every few minutes returns the credits of requests that never
finished (a worker restarted mid-call), so a hold is never stuck.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.billing import ledger, pricing, services
from apps.core import activity, runtime
from apps.core.models import ControlPanel, LogCategory

from .models import ACTIVE, Job, Kind, Status

logger = logging.getLogger(__name__)

# A request still queued or being made after this is given up, its credits
# returned. Generous: a long reasoning answer or a high-quality image takes
# minutes, never twenty.
STALE = timedelta(minutes=20)


class NotEnough(Exception):
    """The balance cannot cover the most the request can cost."""

    def __init__(self, hold, balance):
        super().__init__(f"needs {hold}, has {balance}")
        self.hold, self.balance = hold, balance


class TooMany(Exception):
    """The user already has as many requests being made as allowed."""


class LimitReached(Exception):
    """The request would take the user past the daily limit they set themselves."""

    def __init__(self, limit, spent, hold):
        super().__init__(f"limit {limit}, spent {spent}, needs {hold}")
        self.limit, self.spent, self.hold = limit, spent, hold


def spent_today(account) -> int:
    """Credits charged today, plus what is held for requests being made."""
    from django.db.models import Sum

    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    charged = Job.objects.filter(account=account, status=Status.DONE,
                                 created_at__gte=start).aggregate(total=Sum("charged"))
    held = Job.objects.filter(account=account, status__in=ACTIVE).aggregate(total=Sum("hold"))
    return (charged["total"] or 0) + (held["total"] or 0)


def demo() -> bool:
    return ControlPanel.get().demo_mode


def may_use(account) -> bool:
    """Testers always; everyone once the tools are open (and not in Demo mode)."""
    if account is None:
        return False
    if account.tester:
        return True
    panel = ControlPanel.get()
    return not panel.demo_mode and panel.open_to_everyone


def busy(account) -> int:
    return Job.objects.filter(account=account, status__in=ACTIVE).count()


def hold_for(estimate_micros, model) -> int:
    return pricing.credits_for_micros(estimate_micros, model)


def start(account, chat_id, kind, model, estimate_micros, **fields) -> Job:
    """
    Hold the most the request can cost and record it, or raise NotEnough,
    TooMany or LimitReached. The caller queues it with launch() once it has
    done its part (the message the answer goes into).
    """
    if busy(account) >= int(runtime.get("JOBS_PER_USER")):
        raise TooMany()
    hold = hold_for(estimate_micros, model)
    balance = ledger.balance(account)
    if hold > balance:
        raise NotEnough(hold, balance)
    from .chat import profile

    limit = profile(account).daily_limit
    if limit:
        spent = spent_today(account)
        if spent + hold > limit:
            raise LimitReached(limit, spent, hold)
    conversation = fields.get("conversation")
    if conversation is not None and "assistant" not in fields:
        fields["assistant_id"] = conversation.assistant_id
    try:
        with transaction.atomic():
            job = Job.objects.create(account=account, kind=kind, model=model, chat_id=chat_id,
                                     estimate_micros=estimate_micros, hold=hold, demo=demo(),
                                     **fields)
            services.hold(job)
    except ledger.InsufficientCredits:   # spent elsewhere a moment ago
        raise NotEnough(hold, ledger.balance(account)) from None
    return job


def launch(job):
    from . import tasks

    tasks.run_job.delay(job.pk)


def run(job_id):
    """Make it, once: a second delivery of the same task finds it already taken."""
    taken = Job.objects.filter(pk=job_id, status=Status.QUEUED).update(
        status=Status.RUNNING, started_at=timezone.now())
    if not taken:
        return "not queued"
    job = Job.objects.select_related("account", "model", "model__provider",
                                     "conversation").get(pk=job_id)
    from apps.core import languages

    from . import chat, images, notify, voice

    maker = {Kind.CHAT: chat.answer, Kind.IMAGE: images.make, Kind.EDIT: images.make,
             Kind.TRANSCRIBE: voice.transcribe, Kind.SPEECH: voice.speak}[job.kind]
    with languages.activate_for(job.account):
        try:
            maker(job)
        except Exception as exc:
            logger.exception("request #%s failed", job.pk)
            if failed(job, f"{type(exc).__name__}: {exc}"[:500]):
                notify.failed(job)
    return job.status


def charge_preview(job, cost_micros) -> tuple:
    """
    (credits it will be charged, the balance that leaves): for a caption
    sent before the charge, so a result Telegram takes nowhere costs nothing.
    """
    credits = min(pricing.credits_for_micros(cost_micros, job.model), job.hold)
    return credits, ledger.balance(job.account) + job.hold - credits


def done(job, cost_micros, tokens_in=0, tokens_out=0, cached=0) -> int:
    """
    Charge what it really cost (never more than the hold), with the tokens
    the provider reported; the new balance.
    """
    credits = min(pricing.credits_for_micros(cost_micros, job.model), job.hold)
    with transaction.atomic():
        updated = Job.objects.filter(pk=job.pk, status=Status.RUNNING).update(
            status=Status.DONE, cost_micros=cost_micros, charged=credits,
            input_tokens=tokens_in, output_tokens=tokens_out, cached_tokens=cached,
            finished_at=timezone.now(), updated_at=timezone.now())
        if updated:
            services.charge(job, credits)
    if updated:
        job.status, job.cost_micros, job.charged = Status.DONE, cost_micros, credits
        job.input_tokens, job.output_tokens, job.cached_tokens = tokens_in, tokens_out, cached
        activity.info(LogCategory.STUDIO, f"{job.kind}_done",
                      f"#{job.pk} {job.account}: 🪙 {credits} of 🪙 {job.hold} held "
                      f"(${cost_micros / 1e6:.4f})", account=job.account,
                      detail={"job": job.pk, "charged": credits, "hold": job.hold})
    return ledger.balance(job.account)


def failed(job, error, by="system"):
    """Not done: the whole hold goes back. Idempotent."""
    with transaction.atomic():
        updated = Job.objects.filter(pk=job.pk, status__in=ACTIVE).update(
            status=Status.FAILED, error=str(error)[:500], finished_at=timezone.now(),
            updated_at=timezone.now())
        if updated:
            services.give_back(job, by=by)
    if updated:
        job.status, job.error = Status.FAILED, str(error)[:500]
        activity.warn(LogCategory.STUDIO, f"{job.kind}_failed",
                      f"#{job.pk} {job.account}: {error}"[:500], account=job.account,
                      detail={"job": job.pk})
    return bool(updated)


def sweep() -> int:
    """Requests that never finished: given up, credits returned, user told."""
    from apps.core import languages

    from . import notify

    cutoff = timezone.now() - STALE
    stale = list(Job.objects.filter(status__in=ACTIVE, created_at__lt=cutoff)
                 .select_related("account", "model"))
    for job in stale:
        if failed(job, "never finished: given up by the sweep"):
            with languages.activate_for(job.account):
                notify.failed(job)
    return len(stale)


def note_message(job, message_id):
    Job.objects.filter(pk=job.pk).update(message_id=message_id)
    job.message_id = message_id


def touch_conversation(conversation):
    """The chat the user is in is the one touched last."""
    from .models import Conversation

    Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())

