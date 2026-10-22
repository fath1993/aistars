from aistars.settings import *  # noqa: F401,F403

import os as _os

from celery.schedules import crontab

from apps.core import hosts as _hosts

SECRET_KEY = env('SECRET_KEY')

DEBUG = env.bool('DJANGO_DEBUG', default=False)


def _env_list(name):
    return [item.strip() for item in _os.environ.get(name, "").split(",")
            if item.strip()]


# The .env lists are a FLOOR that is never replaced; PUBLIC_BASE_URL from the
# admin is added on top. If the database is unreachable or the setting is
# unset, the floor alone still lets you reach the admin.
ALLOWED_HOSTS = _hosts.allowed_hosts(
    _env_list("ALLOWED_HOSTS") or ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = _hosts.trusted_origins(_env_list("CSRF_TRUSTED_ORIGINS"))

# Cloudflare terminates TLS and reaches the server over plain HTTP; its
# X-Forwarded-Proto: https is passed through by the edge and the project nginx.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True


# ============================================================
# Database
# ============================================================

DATABASES['default'] = {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': env("POSTGRES_DB"),
    'USER': env("POSTGRES_USER"),
    'PASSWORD': env("POSTGRES_PASSWORD"),
    # The compose service name, resolved over aistars-net.
    'HOST': env("POSTGRES_HOST", default="aistars-db"),
    'PORT': env("POSTGRES_PORT", default="5432"),
}


# ============================================================
# Redis: DB 2 cache, DB 3 Celery
# ============================================================

REDIS_HOST = env("REDIS_HOST", default="aistars-redis")
REDIS_PORT = env.int("REDIS_PORT", default=6379)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"redis://{REDIS_HOST}:{REDIS_PORT}/2",
        "KEY_PREFIX": "aistars",
    }
}


# ============================================================
# Celery
# ============================================================

CELERY_BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/3"
CELERY_RESULT_BACKEND = f"redis://{REDIS_HOST}:{REDIS_PORT}/3"

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'

# Celery's own default queue is "celery"; the worker listens to "default".
# Without this, any task not routed explicitly goes to a queue nobody reads.
CELERY_TASK_DEFAULT_QUEUE = 'default'

CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Crontabs run in UTC (CELERY_TIMEZONE), not Europe/Bucharest.
CELERY_BEAT_SCHEDULE = {
    'purge_activity_log': {
        'task': 'apps.core.tasks.purge_activity_log',
        'schedule': crontab(hour=4, minute=30),
    },
    'purge_telegram_updates': {
        'task': 'apps.core.tasks.purge_telegram_updates',
        'schedule': crontab(hour=4, minute=40),
    },
    'purge_spend_ledger': {
        'task': 'apps.core.tasks.purge_spend_ledger',
        'schedule': crontab(hour=4, minute=0, day_of_week=0),
    },
    # Untranslated texts of languages with Translate on: after a deploy, or
    # once an API key is added, without anyone pressing anything.
    'translate_missing': {
        'task': 'apps.core.tasks.translate_missing',
        'schedule': crontab(minute='*/30'),
    },
    # Requests that never finished (a worker restarted mid-call) get their
    # credits back.
    'sweep_jobs': {
        'task': 'apps.studio.tasks.sweep_jobs',
        'schedule': crontab(minute='*/5'),
    },
}
