"""
What the bot is waiting for from a user, if anything: a description, a photo,
a voice note, a text to read aloud, or a choice on a summary. One at a time,
in the cache; it expires after half an hour. Without it, anything typed is a
chat message.
"""

from django.core.cache import cache

TTL = 30 * 60


def _key(account):
    return f"studio:wait:{account.pk}"


def waiting(account):
    """The draft {"step": …, …}, or None."""
    return cache.get(_key(account))


def wait(account, step, **data):
    cache.set(_key(account), {**data, "step": step}, TTL)


def stop(account):
    cache.delete(_key(account))
