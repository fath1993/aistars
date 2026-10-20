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
