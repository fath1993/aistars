"""
Shared settings. Environment-specific values live in c_settings/.

`.env` holds only what must exist before the database does: SECRET_KEY,
database credentials, DEBUG, hosts and APP_VERSION. Everything else -- bot
token, webhook secret, public URL -- is a runtime setting edited in the admin
(apps/core/runtime.py).
"""

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
env_path = os.path.join(Path(__file__).resolve().parent, '.env')
environ.Env.read_env(env_path)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # --- project apps ---
    'apps.core',
    'apps.ai',
    'apps.billing',
    'apps.safety',
    'apps.studio',
    'apps.finance',
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    # Re-reads CSRF_TRUSTED_ORIGINS per request so it can follow
    # PUBLIC_BASE_URL from the admin; see apps.core.middleware.
    "apps.core.middleware.DynamicCsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

X_FRAME_OPTIONS = "DENY"

ROOT_URLCONF = "aistars.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / 'templates'],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

APP_VERSION = env("APP_VERSION", default="0.0.0")

WSGI_APPLICATION = "aistars.wsgi.application"
ASGI_APPLICATION = "aistars.asgi.application"

# Populated per-environment in c_settings/.
DATABASES = {}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# English is the bot's source text, written in the code, and the fallback
# for anything not translated. The bot's other languages are translated by
# AI into the database (apps/core/i18n.py); the admin itself stays English.
LANGUAGE_CODE = "en"
TIME_ZONE = "Europe/Bucharest"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static'

# Uploaded files: only the owner's invoices (My finances). Never served
# publicly: nginx has no /media/ and Django serves them to superusers only.
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))
MEDIA_URL = "/media/"

# No user uploads, ever: media lives in Telegram (and its archive channel).


# ============================================================
# Telegram
# ============================================================

# Fallbacks only. The admin (Runtime settings) wins once a value is set there.
TELEGRAM_BOT_TOKEN = env('TELEGRAM_BOT_TOKEN', default='')
TELEGRAM_WEBHOOK_SECRET = env('TELEGRAM_WEBHOOK_SECRET', default='')
TELEGRAM_API_BASE = 'https://api.telegram.org'


# ============================================================
# Spend guard
# ============================================================

# Micro-units of USD (1_000_000 = $1.00). Fallbacks for the budgets in the
# Control panel, so a misconfigured database cannot leave spend unbounded.
# Zero is a HARD STOP; a negative value disables the cap.
SPEND_DAILY_CAP_MICROS = env.int('SPEND_DAILY_CAP_MICROS', default=20_000_000)
SPEND_HOURLY_CAP_MICROS = env.int('SPEND_HOURLY_CAP_MICROS', default=5_000_000)

SPEND_LEDGER_RETENTION_DAYS = env.int('SPEND_LEDGER_RETENTION_DAYS', default=90)
