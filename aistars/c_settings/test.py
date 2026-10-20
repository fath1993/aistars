"""
Settings for the test suite. Runs anywhere, including inside the production
container, with no database or Redis of its own:

    python manage.py test --settings=aistars.c_settings.test
"""

from aistars.settings import *  # noqa: F401,F403

SECRET_KEY = env('SECRET_KEY', default='test-only-not-secret')

DEBUG = False

ALLOWED_HOSTS = ['testserver', 'localhost']

DATABASES['default'] = {
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': ':memory:',
}

CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

CELERY_TASK_ALWAYS_EAGER = True

# Every lookup reads the database: a per-process catalog would outlive the
# rollback between tests.
I18N_CACHE = False
