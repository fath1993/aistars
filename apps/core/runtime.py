"""
Runtime settings: the values that change during the application's life.

`.env` keeps only what must exist before the database does. Everything else
lives here, editable in the admin and taking effect on the next read.

Resolution order for any key:

    1. RuntimeSetting row, if one exists and is non-empty
    2. the Django settings value of the same name, as a fallback
    3. the registry's declared default

Rows are created from the registry after every migrate (sync_registry), so a
new setting is one Spec line here -- no data migration.
"""

import logging
import re
from dataclasses import dataclass, field

from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY = "runtime:settings:v1"
# Short rather than zero: a stale value for a few seconds is a far better
# trade than a query per read. save() busts the cache, so an admin edit is
# still effectively immediate.
CACHE_TTL = 30


@dataclass(frozen=True)
class Spec:
    key: str
    group: str
    label: str
    kind: str = "str"          # str | int | float | bool | text
    default: object = ""
    secret: bool = False
    help: str = ""
    # Keys the app cannot run without once it is doing real work.
    required: bool = False
    choices: tuple = field(default_factory=tuple)


REGISTRY = (
    # ---- Telegram -------------------------------------------------------
    Spec("TELEGRAM_BOT_TOKEN", "telegram", "Bot token", secret=True, required=True,
         help="Paste EXACTLY what @BotFather gives you, e.g. "
              "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw — digits, a colon, "
              "then ~35 characters. A leading 'bot' is stripped automatically. "
              "Register the webhook again after changing this."),
    Spec("TELEGRAM_WEBHOOK_SECRET", "telegram", "Webhook secret", secret=True,
         required=True,
         help="Any random string of A-Z a-z 0-9 _ - (1-256 chars); generate "
              "with: openssl rand -hex 32. Passed to setWebhook and returned by "
              "Telegram in a header, never put in the URL. Register the "
              "webhook again after changing it."),
    Spec("TELEGRAM_BOT_USERNAME", "telegram", "Bot username", default="",
         help="Without the @. Leave empty to ask Telegram for it."),
    Spec("PUBLIC_BASE_URL", "telegram", "Public base URL",
         default="", required=True,
         help="Where Telegram can reach this app, scheme and host only, e.g. "
              "https://aistars.example.com — no path, no trailing slash. "
              "The webhook path is appended automatically."),

    Spec("ADMIN_CHAT_ID", "telegram", "Your Telegram ID", kind="int", default="",
         help="Where the bot sends you notices, such as a new credit request. Your "
              "numeric Telegram user ID (ask @userinfobot). Empty: no notices."),
    Spec("SUPPORT_CONTACT", "telegram", "Support contact", default="",
         help="Shown by /support and /paysupport, e.g. @YourUsername or an e-mail "
              "address. Telegram requires bots that take Stars to answer "
              "/paysupport."),

    # ---- Billing ---------------------------------------------------------
    Spec("PROFIT_MARGIN_PERCENT", "billing", "Profit margin (%)", kind="float",
         default=40,
         help="Added on top of what a request costs you. Price in credits = "
              "provider cost ÷ Star value × (1 + margin ÷ 100), rounded up, at least 1. "
              "A model with its own margin (AI → Models) uses that instead. Applies to "
              "the next price shown; never to anything already paid."),
    Spec("STAR_VALUE_USD", "billing", "Value of one Star (USD)", kind="float",
         default=0.013,
         help="What you receive per Telegram Star. One credit is one Star."),

    # ---- AI tools --------------------------------------------------------
    Spec("ARCHIVE_CHAT_ID", "studio", "Archive channel", kind="int", default="",
         help="Filled in by itself: make the bot an admin of a private channel, from your "
              "own Telegram account (Your Telegram ID must be set). Every photo and voice "
              "note sent in, and every image and voice note made, is copied there. "
              "Empty: nothing is archived."),
    Spec("CHAT_CONTEXT_MESSAGES", "studio", "Chat memory (messages)", kind="int", default=20,
         help="How many earlier messages of a chat are sent with each new one, so the AI "
              "remembers the conversation. More costs more on every message."),
    Spec("CHAT_MAX_MESSAGE_CHARS", "studio", "Longest message (characters)", kind="int",
         default=8000,
         help="A longer typed message, or voice note transcript, is refused with a note."),
    Spec("JOBS_PER_USER", "studio", "Requests at once per user", kind="int", default=2,
         help="Chat answers, images and voice notes being made at the same time for one "
              "user. One chat answer at a time per chat whatever this says."),

    # ---- Safety ----------------------------------------------------------
    Spec("SAFETY_STRIKE_LIMIT", "safety", "Strikes before blocking", kind="int", default=3,
         help="A request the safety checks block, or one a provider refuses, is a strike. "
              "At this many the account is blocked by itself. A strike you overturn in "
              "the review queue is taken back."),

    # ---- Housekeeping ----------------------------------------------------
    Spec("ACTIVITY_LOG_RETENTION_DAYS", "general", "Activity log retention (days)",
         kind="int", default=30),
)

BY_KEY = {spec.key: spec for spec in REGISTRY}
GROUPS = ("telegram", "billing", "studio", "safety", "general")


WEBHOOK_PATH = "/bot/webhook/"


def webhook_url() -> str:
    """The one correct URL, built rather than typed."""
    base = str(get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return f"{base}{WEBHOOK_PATH}" if base else ""


def normalise(key: str, value: str) -> str:
    """
    Clean up a pasted value before it is stored.

    The bot token is the one that bites: the API URL is /bot<token>/method, so
    people paste "bot123456:AA..." and every call 404s with no useful error.
    """
    value = (value or "").strip().strip('"').strip("'")
    if key == "TELEGRAM_BOT_TOKEN" and value.lower().startswith("bot"):
        value = value[3:]
    if key == "PUBLIC_BASE_URL":
        # Query and fragment first, then the trailing slash: in the other
        # order "https://x/?a=1" kept its slash. (Same bug exists in AVideo.)
        for marker in ("?", "#"):
            value = value.split(marker)[0]
        value = value.rstrip("/")
    return value


# Whole-number settings and the values they accept.
RANGES = {
    "CHAT_CONTEXT_MESSAGES": (0, 100),
    "CHAT_MAX_MESSAGE_CHARS": (100, 100_000),
    "JOBS_PER_USER": (1, 10),
}


def validate(key: str, value: str) -> str:
    """Return an error message, or empty string if the value is usable."""
    if not value:
        return ""
    if key == "TELEGRAM_BOT_TOKEN":
        if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", value):
            return ("Doesn't look like a bot token. Expected digits, a colon, "
                    "then ~35 characters, e.g. 123456789:AAHdqTcv…")
    if key == "TELEGRAM_WEBHOOK_SECRET":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
            return ("Telegram only accepts A-Z a-z 0-9 _ - here, 1-256 "
                    "characters. Try: openssl rand -hex 32")
    if key == "PUBLIC_BASE_URL":
        if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?", value):
            return ("Scheme and host only, e.g. https://aistars.example.com "
                    "— no path, no query string.")
        if value.startswith("http://"):
            return "Telegram requires HTTPS for webhooks."
    if key == "ADMIN_CHAT_ID" and not re.fullmatch(r"-?\d{5,15}", value):
        return "A Telegram user ID is digits only, e.g. 123456789."
    if key == "ARCHIVE_CHAT_ID" and not re.fullmatch(r"-100\d{5,15}", value):
        return "A channel ID starts with -100, e.g. -1001234567890."
    if key == "SAFETY_STRIKE_LIMIT":
        if not value.isdigit() or not 1 <= int(value) <= 100:
            return "A whole number from 1 to 100."
    if key in RANGES:
        low, high = RANGES[key]
        if not value.isdigit() or not low <= int(value) <= high:
            return f"A whole number from {low} to {high}."
    if key in ("PROFIT_MARGIN_PERCENT", "STAR_VALUE_USD"):
        try:
            number = float(value)
        except ValueError:
            return "A number, e.g. 40 or 0.013."
        if key == "PROFIT_MARGIN_PERCENT" and not 0 <= number <= 1000:
            return "Between 0 and 1000."
        if key == "STAR_VALUE_USD" and not 0 < number < 1:
            return "More than 0 and less than 1 USD."
    return ""


def _coerce(spec: Spec, raw):
    if raw is None or raw == "":
        return None
    if spec.kind == "int":
        return int(raw)
    if spec.kind == "float":
        return float(raw)
    if spec.kind == "bool":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return str(raw)


def _load() -> dict:
    from .models import RuntimeSetting

    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    try:
        rows = dict(RuntimeSetting.objects.values_list("key", "value"))
    except Exception:
        # Table not created yet, or the database is unreachable. Fall back to
        # settings rather than taking the whole app down.
        logger.debug("runtime settings unavailable; using Django settings")
        rows = {}

    cache.set(CACHE_KEY, rows, CACHE_TTL)
    return rows


def invalidate():
    cache.delete(CACHE_KEY)


def sync_registry() -> int:
    """
    Make sure every registry entry has a row. Returns how many were created.

    Describing fields (label, group, help...) follow the registry; the value is
    never overwritten. A new row starts from the .env value when there is one,
    so moving a setting into the admin does not blank a working credential.
    """
    from django.conf import settings as dj

    from .models import RuntimeSetting

    created_count = 0
    for spec in REGISTRY:
        row, created = RuntimeSetting.objects.update_or_create(
            key=spec.key,
            defaults={
                "group": spec.group,
                "label": spec.label,
                "kind": spec.kind,
                "is_secret": spec.secret,
                "help_text": spec.help,
                "default_value": str(spec.default),
            },
        )
        if created:
            created_count += 1
            existing = getattr(dj, spec.key, None)
            if existing not in (None, ""):
                row.value = str(existing)
                row.save(update_fields=["value"])
    invalidate()
    return created_count


# Consulted before anything else, so tests can set a value at the same layer
# the app reads it from.
_overrides: dict = {}


class override:
    """Context manager and decorator: `with runtime.override(KEY=value):`"""

    def __init__(self, **values):
        unknown = set(values) - set(BY_KEY)
        if unknown:
            raise KeyError(f"not in the runtime registry: {sorted(unknown)}")
        self.values = values
        self.previous = {}

    def __enter__(self):
        self.previous = dict(_overrides)
        _overrides.update(self.values)
        return self

    def __exit__(self, *exc):
        _overrides.clear()
        _overrides.update(self.previous)
        return False

    def __call__(self, target):
        """
        Decorate a function or a TestCase class.

        Class support matters: decorating a class with a function wrapper
        stops the runner collecting it, and the suite reports green while the
        tests silently do not exist.
        """
        import functools

        if isinstance(target, type):
            return self._decorate_class(target)

        @functools.wraps(target)
        def wrapper(*args, **kwargs):
            with self:
                return target(*args, **kwargs)
        return wrapper

    def _decorate_class(self, cls):
        values = self.values
        original_setup = cls.setUp
        original_teardown = cls.tearDown

        # A stack per test: two decorated classes (or a decorated class and
        # its decorated base) each undo their own override, last in first out.
        def setUp(inner):
            entered = override(**values)
            entered.__enter__()
            stack = inner.__dict__.setdefault("_runtime_overrides", [])
            stack.append(entered)
            try:
                original_setup(inner)
            except BaseException:
                stack.pop().__exit__(None, None, None)
                raise

        def tearDown(inner):
            try:
                original_teardown(inner)
            finally:
                inner._runtime_overrides.pop().__exit__(None, None, None)

        cls.setUp = setUp
        cls.tearDown = tearDown
        return cls


def get(key: str):
    spec = BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"{key} is not in the runtime settings registry")

    if key in _overrides:
        return _overrides[key]

    value = _coerce(spec, _load().get(key))
    if value is not None:
        return value

    from django.conf import settings

    fallback = getattr(settings, key, None)
    if fallback not in (None, ""):
        return fallback
    return spec.default


def missing_required() -> list:
    return [spec.key for spec in REGISTRY if spec.required and not get(spec.key)]
