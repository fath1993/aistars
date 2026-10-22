from decimal import Decimal

from django.db import models


class ProviderKind(models.TextChoices):
    OPENAI = "openai", "OpenAI"
    ANTHROPIC = "anthropic", "Anthropic"


DEFAULT_BASE_URL = {
    ProviderKind.OPENAI: "https://api.openai.com",
    ProviderKind.ANTHROPIC: "https://api.anthropic.com",
}


class Provider(models.Model):
    """An AI company we call, with its API key. One row per company."""

    kind = models.CharField(max_length=16, choices=ProviderKind.choices, unique=True)
    api_key = models.TextField(blank=True, help_text="Kept here, never shown again in full.")
    admin_key = models.TextField(
        blank=True, help_text="Optional, OpenAI only: an Admin key, read-only use, so My "
                              "finances can show OpenAI's own cost figures next to the bot's.")
    base_url = models.URLField(blank=True,
                               help_text="Leave empty for the provider's own API address.")
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(null=True, blank=True,
                                     help_text="When its list of models was last read.")

    class Meta:
        ordering = ("kind",)

    def __str__(self):
        return self.get_kind_display()

    @property
    def endpoint(self) -> str:
        return (self.base_url or DEFAULT_BASE_URL[self.kind]).rstrip("/")

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.api_key.strip())

    def masked_key(self) -> str:
        key = self.api_key.strip()
        return f"••••{key[-4:]}" if key else "not set"


class Use(models.TextChoices):
    """What a model is for. Each use has its own prices (see AIModel)."""

    CHAT = "chat", "Chat"
    IMAGE = "image", "Images"
    TRANSCRIPTION = "transcription", "Voice to text"
    SPEECH = "speech", "Text to speech"


class AIModel(models.Model):
    """
    One model of a provider, and what it costs. Every call is priced from
    these and counts against the Control panel's caps; users are charged that
    cost in credits, with the margin (billing.pricing).

    Prices by use:
        chat           input_price, output_price: USD per 1M text tokens
        images         input_price: per 1M prompt tokens; image_input_price: per
                       1M tokens of photos sent in; output_price: per 1M image
                       tokens made
        voice to text  unit_price: USD per minute of audio
        text to speech unit_price: USD per 1M characters read
    """

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="models")
    model_id = models.CharField(max_length=100,
                                help_text="Exactly as the provider names it, e.g. gpt-6-luna.")
    label = models.CharField(max_length=100, blank=True,
                             help_text="The name users see, e.g. GPT-6 Luna.")
    use = models.CharField(max_length=16, choices=Use.choices, default=Use.CHAT,
                           db_index=True)
    enabled = models.BooleanField(
        default=True, help_text="May be called at all (also for translation and safety).")
    offered = models.BooleanField(
        default=False, help_text="Offered to users in the bot. Set its prices first.")
    is_default = models.BooleanField(
        "default for users", default=False,
        help_text="What users get unless they choose another: new chats, images, voice. "
                  "One per use.")
    tagline = models.CharField(
        max_length=80, blank=True,
        help_text="One short line users see under the name, e.g. “Fast and cheap, for "
                  "everyday questions”. Translated like the bot's texts.")
    sees_images = models.BooleanField(
        default=False, help_text="Chat: reads photos sent with a message. Images: changes "
                                 "a photo sent in (Edit a photo).")

    input_price = models.DecimalField(max_digits=10, decimal_places=4, default=0,
                                      help_text="USD per 1M input (text) tokens.")
    output_price = models.DecimalField(max_digits=10, decimal_places=4, default=0,
                                       help_text="USD per 1M output tokens (images: image "
                                                 "tokens made).")
    image_input_price = models.DecimalField(
        max_digits=10, decimal_places=4, default=0,
        help_text="Images: USD per 1M tokens of the photos sent in.")
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=4, default=0,
        help_text="Voice to text: USD per minute of audio. Text to speech: USD per 1M "
                  "characters.")
    # The providers keep the start of a chat request (instructions, earlier
    # messages, files) in a cache: writing it costs more than plain input,
    # reading it again much less.
    cache_write_factor = models.DecimalField(
        max_digits=5, decimal_places=3, default=Decimal("1.25"),
        help_text="Chat: input written to the provider's cache costs this times the input "
                  "price (1.25 for 30 minutes at OpenAI, 5 minutes at Anthropic).")
    cache_read_factor = models.DecimalField(
        max_digits=5, decimal_places=3, default=Decimal("0.1"),
        help_text="Chat: input read from the provider's cache costs this times the input "
                  "price (0.1; 0.05 for GPT-6.1 Sol and Claude Sonnet 5.5).")
    max_output_tokens = models.PositiveIntegerField(
        default=4000, help_text="Chat: the longest answer, reasoning included. The hold "
                                "before an answer is priced at this.")
    margin = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Its own profit margin (%). Empty: Runtime settings → Profit margin.")
    sort_order = models.PositiveSmallIntegerField(default=100)

    default_for_translation = models.BooleanField(
        default=False, help_text="Used for every language that has no model of its own.")
    default_for_safety = models.BooleanField(
        default=False, help_text="The policy judge: reads every image request, and its "
                                 "photo, before anything is made. It must see images.")

    # From the provider's list of models (Sync models).
    listed = models.BooleanField(
        null=True, blank=True,
        help_text="In the provider's list of models at the last sync. Empty: not checked.")

    class Meta:
        ordering = ("use", "sort_order", "provider__kind", "model_id")
        unique_together = ("provider", "model_id")
        verbose_name = "model"

    def __str__(self):
        return self.label or self.model_id

    @property
    def usable(self) -> bool:
        return self.enabled and self.provider.usable

    @property
    def priced(self) -> bool:
        if self.use in (Use.TRANSCRIPTION, Use.SPEECH):
            return self.unit_price > 0
        return self.output_price > 0

    @property
    def ready(self) -> bool:
        """Users can have it: on, offered, priced, and its provider has a key."""
        return self.offered and self.priced and self.usable

    def ready_for(self, demo=False) -> bool:
        """The same; in Demo mode, which calls no provider, no key is needed."""
        return self.offered and self.priced and self.enabled and (demo or self.provider.usable)

    # ---- what a call costs, in micro-USD ---------------------------------

    def cost_micros(self, input_tokens: int, output_tokens: int,
                    image_input_tokens: int = 0, cached: int = 0, written: int = 0) -> int:
        """
        Tokens x USD per 1M tokens is exactly micro-USD. `input_tokens` is all
        the input; `cached` of it was read from the provider's cache and
        `written` of it written to it.
        """
        price = float(self.input_price)
        fresh = max(input_tokens - cached - written, 0)
        return round(fresh * price + cached * price * float(self.cache_read_factor)
                     + written * price * float(self.cache_write_factor)
                     + image_input_tokens * float(self.image_input_price)
                     + output_tokens * float(self.output_price))

    def audio_cost_micros(self, seconds: float) -> int:
        """Voice to text: per minute, by the second."""
        return round(float(self.unit_price) * 1_000_000 * max(seconds, 1) / 60)

    def speech_cost_micros(self, characters: int) -> int:
        """Text to speech: characters x USD per 1M characters is micro-USD."""
        return round(characters * float(self.unit_price))

    # ---- one default of each kind ----------------------------------------

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Ticking a default here unticks it everywhere else.
        for flag in ("default_for_translation", "default_for_safety"):
            if getattr(self, flag):
                AIModel.objects.exclude(pk=self.pk).filter(**{flag: True}).update(**{flag: False})
        if self.is_default:
            AIModel.objects.exclude(pk=self.pk).filter(use=self.use, is_default=True).update(
                is_default=False)

    @classmethod
    def translation_default(cls):
        return cls.objects.select_related("provider").filter(
            default_for_translation=True).first()

    @classmethod
    def safety_default(cls):
        return cls.objects.select_related("provider").filter(default_for_safety=True).first()

    @classmethod
    def offered_for(cls, use, demo=False) -> list:
        """What users can choose for `use` right now, best first."""
        rows = cls.objects.select_related("provider").filter(use=use, offered=True,
                                                             enabled=True)
        return [model for model in rows if model.ready_for(demo)]

    @classmethod
    def default_for(cls, use, demo=False):
        """The default for `use` if users can have it, else the first that is ready."""
        offered = cls.offered_for(use, demo)
        return next((model for model in offered if model.is_default), None) or (
            offered[0] if offered else None)


class Purpose(models.TextChoices):
    """What an AI call was for: a user's request, or the bot's own work."""

    CHAT = "chat", "Chat answers"
    IMAGE = "image", "Images"
    TRANSCRIPTION = "transcription", "Voice to text"
    SPEECH = "speech", "Text to speech"
    SAFETY_JUDGE = "safety_judge", "Safety judge"
    TRANSLATION = "translation", "Translations"
    CHAT_MEMORY = "chat_memory", "Chat memory"
    CHAT_TITLE = "chat_title", "Chat titles"
    FIND_AGENTS = "find_agents", "Find for me"
    DRAFT_AGENTS = "draft_agents", "Drafted agents"
    OTHER = "other", "Other"


# Paid for by users (held and charged in credits); the rest is the bot's own
# work, which the owner pays.
USER_PURPOSES = (Purpose.CHAT, Purpose.IMAGE, Purpose.TRANSCRIPTION, Purpose.SPEECH)


class AICall(models.Model):
    """
    One call to an AI provider and what it cost, as the bot priced it from the
    provider's report: the owner's costs, call by call (My finances). Kept.
    """

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    provider = models.CharField(max_length=16, choices=ProviderKind.choices, db_index=True)
    model = models.CharField(max_length=128)
    purpose = models.CharField(max_length=24, choices=Purpose.choices, db_index=True)
    cost_micros = models.BigIntegerField(default=0, help_text="USD × 1,000,000.")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ("-at",)
        indexes = [models.Index(fields=["provider", "at"])]
        verbose_name = "AI call"

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.provider} {self.purpose} ${self.cost_micros / 1e6:.4f}"
