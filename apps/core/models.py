from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AccountStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    BLOCKED = "blocked", "Blocked"


class TranslationStatus(models.TextChoices):
    IDLE = "idle", "—"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Translating"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class Language(models.Model):
    """
    A language of the world. English is the source: the bot's texts are
    written in it, in the code. Any other language is translated by AI
    (`translate`) and offered to users (`used`).
    """

    code = models.CharField(max_length=16, primary_key=True,
                            help_text="ISO 639-1, lower case: ro, pt-br, zh-hant.")
    name = models.CharField(max_length=64, default="", help_text="In English.")
    native_name = models.CharField(max_length=64, default="",
                                   help_text="In the language itself.")
    used = models.BooleanField(default=False, help_text="Offered to users in the bot.")
    translate = models.BooleanField(
        default=False,
        help_text="Keep it translated by AI: missing texts are translated in the "
                  "background, now and after every deploy.")
    translator = models.ForeignKey(
        "ai.AIModel", null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Empty: the AI model marked as the default for translation.")
    status = models.CharField(max_length=8, choices=TranslationStatus.choices,
                              default=TranslationStatus.IDLE)
    status_detail = models.CharField(max_length=255, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "name")

    def __str__(self):
        return self.native_name or self.code


class SourceText(models.Model):
    """
    One English text of the bot, found in the code (i18n.extract). Texts no
    longer in the code are kept, inactive, with their translations.
    """

    text = models.TextField(unique=True)
    location = models.CharField(max_length=255, blank=True,
                                help_text="Where it is in the code.")
    active = models.BooleanField(default=True)
    first_seen = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        verbose_name = "text"

    def __str__(self):
        return self.text[:80]


class Translation(models.Model):
    """One text in one language, by an AI model or by hand (manual)."""

    language = models.ForeignKey(Language, on_delete=models.CASCADE,
                                 related_name="translations")
    source = models.ForeignKey(SourceText, on_delete=models.CASCADE,
                               related_name="translations")
    text = models.TextField()
    model = models.CharField(max_length=100, blank=True,
                             help_text="The AI model that wrote it; empty when written by hand.")
    manual = models.BooleanField(
        default=False, help_text="Written or corrected by hand: retranslating keeps it.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("language", "source")
        ordering = ("source_id",)

    def __str__(self):
        return self.text[:80]


class Country(models.Model):
    """
    A country, known from the calling code of a user's phone number.

    Only users whose number belongs to an allowed country can use the bot.
    """

    code = models.CharField(max_length=2, primary_key=True,
                            help_text="ISO 3166 code, e.g. RO.")
    name = models.CharField(max_length=64)
    calling_code = models.PositiveSmallIntegerField(help_text="e.g. 40 for +40.")
    allowed = models.BooleanField(default=False)
    default_language = models.ForeignKey(
        Language, null=True, blank=True, on_delete=models.SET_NULL,
        help_text="For users from this country who have not chosen a language. "
                  "Empty: their Telegram language, if it is enabled, else English.")

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "countries"

    @property
    def flag(self) -> str:
        """The flag emoji, built from the code's two regional indicator letters."""
        return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in self.code)

    def __str__(self):
        return f"{self.flag} {self.name}"


class Account(TimestampedModel):
    """
    Everyone who has ever messaged the bot.

    Nothing works until the user shares their own phone number with Telegram's
    contact button (registered_at). The number's country must be allowed in
    the admin. Credits are what gate spending. Blocking is the one manual
    control, set in the admin or by the safety strikes.
    """

    telegram_user_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=64, blank=True)
    display_name = models.CharField(max_length=128, blank=True)
    language_code = models.CharField(max_length=8, blank=True)

    # Shared through Telegram's contact button, never typed, and only the
    # user's own number. Telegram verified it by SMS when the account was made.
    phone = models.CharField(max_length=32, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    # From the phone number's calling code. Empty for numbers with no country
    # (e.g. Telegram's anonymous +888 numbers): never allowed.
    country = models.ForeignKey(Country, null=True, blank=True,
                                on_delete=models.PROTECT, related_name="accounts")
    # Chosen in the bot. Empty means automatic: the country's default language,
    # else the Telegram language, else English (see languages.resolve).
    language = models.CharField(max_length=16, blank=True)

    status = models.CharField(
        max_length=16, choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE, db_index=True,
    )
    # Requests blocked by the safety checks. The account is blocked once this
    # reaches the limit set in the admin.
    strikes = models.PositiveSmallIntegerField(default=0)
    # Testers see the AI tools before everyone does (Control panel → everyone
    # can use the tools), and are the only ones who see Demo mode.
    tester = models.BooleanField(
        default=False, db_index=True,
        help_text="Sees the AI tools in Demo mode, and before they are open to everyone.")

    last_seen_at = models.DateTimeField(null=True, blank=True)

    # The single panel message that menus edit in place, rather than
    # appending a new one per tap and burying the chat.
    ui_message_id = models.BigIntegerField(null=True, blank=True)

    @property
    def is_blocked(self) -> bool:
        return self.status == AccountStatus.BLOCKED

    @property
    def is_registered(self) -> bool:
        return self.registered_at is not None

    @property
    def region_allowed(self) -> bool:
        return self.country is not None and self.country.allowed

    def __str__(self):
        label = self.display_name or self.username or str(self.telegram_user_id)
        return f"{label} ({self.get_status_display()})"


class ControlPanel(models.Model):
    """
    Single-row kill switch and spend budgets, editable in the admin.

    Exists so stopping all spending never requires a deploy.
    """

    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1)

    demo_mode = models.BooleanField(
        default=True,
        help_text="Every AI tool answers with a demo after a few seconds and nothing is "
                  "sent to any provider; testers only. Credits are held and charged as "
                  "usual. Off: each model uses its provider (AI → Providers).")
    open_to_everyone = models.BooleanField(
        "everyone can use the tools", default=False,
        help_text="With Demo mode off. Off: only accounts marked Tester can use them.")

    paused = models.BooleanField(
        default=False,
        help_text="Kill switch. While on, nothing new is sent to a paid API.")
    pause_reason = models.CharField(max_length=255, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)

    # Whole USD rather than the micro-units the ledger uses, because this is
    # the number the owner actually types. Blank falls back to settings.
    daily_budget = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="USD per day. New paid work is refused past this. "
                  "Blank = settings default. 0 = stop everything. "
                  "Negative = no limit.",
    )
    hourly_budget = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="USD per hour; catches a runaway inside the hour it starts. "
                  "Blank = settings default. 0 = stop everything. "
                  "Negative = no limit.",
    )

    class Meta:
        verbose_name = "Control panel"
        verbose_name_plural = "Control panel"

    def save(self, *args, **kwargs):
        # Django picks INSERT vs UPDATE from _state.adding, not the pk, so
        # forcing singleton=1 on a fresh instance would collide. Force UPDATE.
        self.singleton = 1
        if ControlPanel.objects.filter(pk=1).exists():
            kwargs.pop("force_insert", None)
            kwargs["force_update"] = True
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        panel, _ = cls.objects.get_or_create(singleton=1)
        return panel

    def daily_cap_micros(self) -> int:
        if self.daily_budget is None:
            from django.conf import settings

            return settings.SPEND_DAILY_CAP_MICROS
        return int(self.daily_budget * 1_000_000)

    def hourly_cap_micros(self) -> int:
        if self.hourly_budget is None:
            from django.conf import settings

            return settings.SPEND_HOURLY_CAP_MICROS
        return int(self.hourly_budget * 1_000_000)

    def __str__(self):
        return "paused" if self.paused else "running"


class RuntimeSetting(models.Model):
    """
    One admin-editable configuration value.

    A row per key rather than typed columns, so adding a setting is a registry
    entry, not a schema change. The registry in runtime.py owns the type,
    default and help text; this table only stores the value.
    """

    key = models.CharField(max_length=64, unique=True)
    value = models.TextField(blank=True)

    group = models.CharField(max_length=32, db_index=True)
    label = models.CharField(max_length=128)
    kind = models.CharField(max_length=8, default="str")
    is_secret = models.BooleanField(default=False)
    help_text = models.TextField(blank=True)
    default_value = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["group", "label"]
        verbose_name = "Runtime setting"

    def clean(self):
        from django.core.exceptions import ValidationError

        from . import runtime

        self.value = runtime.normalise(self.key, self.value)
        problem = runtime.validate(self.key, self.value)
        if problem:
            raise ValidationError({"value": problem})

    def save(self, *args, **kwargs):
        from . import runtime

        # Normalised here too, not only in clean(): shell edits skip clean(),
        # and a token pasted with a "bot" prefix fails unreadably.
        self.value = runtime.normalise(self.key, self.value)
        super().save(*args, **kwargs)
        runtime.invalidate()

    def masked(self) -> str:
        if not self.value:
            return "(unset)"
        if not self.is_secret:
            return self.value if len(self.value) <= 60 else self.value[:57] + "..."
        return "•" * 8 + self.value[-4:]

    def __str__(self):
        return self.key


class SpendLedger(models.Model):
    """
    What has been spent on paid APIs, bucketed by hour, in micro-USD.

    In the database, not only the cache: a cache flush must not reset the
    day's total and re-arm the whole daily budget.
    """

    date = models.DateField(db_index=True)
    hour = models.PositiveSmallIntegerField()

    calls = models.PositiveIntegerField(default=0)
    cost_micros = models.BigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["date", "hour"], name="uniq_ledger_bucket"),
        ]
        indexes = [models.Index(fields=["-date", "-hour"])]
        ordering = ["-date", "-hour"]
        verbose_name = "Spend ledger bucket"

    def __str__(self):
        return f"{self.date} {self.hour:02d}:00 — {self.calls} calls, {self.cost_micros}"


class LogLevel(models.TextChoices):
    DEBUG = "debug", "Debug"
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class LogCategory(models.TextChoices):
    WEBHOOK = "webhook", "Webhook"
    ACCOUNT = "account", "Account"
    BUDGET = "budget", "Budget"
    ADMIN = "admin", "Admin action"
    SYSTEM = "system", "System"
    AI = "ai", "AI"
    TRANSLATION = "translation", "Translation"
    BILLING = "billing", "Billing"
    STUDIO = "studio", "AI tools"
    SAFETY = "safety", "Safety"


class ActivityLog(models.Model):
    """
    What the application did, in the admin.

    Append-only and explicit: entries are written at points that mean
    something to an operator, not scraped from every log line.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    level = models.CharField(max_length=8, choices=LogLevel.choices,
                             default=LogLevel.INFO, db_index=True)
    category = models.CharField(max_length=16, choices=LogCategory.choices,
                                db_index=True)
    event = models.CharField(max_length=64, db_index=True)
    message = models.TextField(blank=True)

    account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="activity",
    )
    detail = models.JSONField(default=dict, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at", "level"]),
            models.Index(fields=["category", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.level}] {self.category}/{self.event}"
