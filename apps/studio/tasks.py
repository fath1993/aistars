from celery import shared_task

from . import jobs


@shared_task
def run_job(job_id):
    """A request, made in the background (jobs.run takes it once)."""
    return jobs.run(job_id)


@shared_task
def sweep_jobs():
    """Requests that never finished get their credits back."""
    return jobs.sweep()
