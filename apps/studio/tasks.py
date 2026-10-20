from celery import shared_task

from . import jobs


@shared_task
def run_job(job_id):
    """A request, made in the background (jobs.run takes it once)."""
    return jobs.run(job_id)


@shared_task
def sweep_jobs():
    """Requests that never finished get their credits back; stale album pieces go."""
    from . import album

    album.forget_stale()
    return jobs.sweep()


@shared_task
def flush_album(account_id, group, quiet=None):
    """Photos and files the user sent as one message go on together."""
    from . import album

    return album.flush(account_id, group, album.QUIET if quiet is None else quiet)


@shared_task
def find_agents(account_id, chat_id, need):
    """✨ Find for me: the AI picks agents for what the user described."""
    from apps.core import languages
    from apps.core.models import Account

    from . import bot

    account = Account.objects.select_related("country").get(pk=account_id)
    with languages.activate_for(account):
        bot.found(account, chat_id, need)


@shared_task
def draft_agents(category_id, count=3):
    """For the owner: new agents for a category, written by AI, switched off."""
    from apps.ai.clients import AIError
    from apps.core import activity
    from apps.core.models import LogCategory

    from . import agents
    from .models import Category

    category = Category.objects.get(pk=category_id)
    try:
        made = agents.draft(category, count)
    except AIError as exc:
        activity.warn(LogCategory.STUDIO, "draft_agents_failed",
                      f"Drafting agents for {category} failed: {exc}")
        return 0
    activity.info(LogCategory.STUDIO, "agents_drafted",
                  f"{len(made)} agent(s) drafted for {category}, switched off: "
                  + ", ".join(agent.slug for agent in made))
    return len(made)


@shared_task
def tidy_chat(conversation_id):
    """After an answer: name a new chat, fold old messages into its memory."""
    from . import memory

    return memory.tidy(conversation_id)


@shared_task
def purge_chats():
    """Chats unused for CHAT_KEEP_DAYS go, with their words everywhere (privacy.py)."""
    from . import privacy

    return privacy.purge()
