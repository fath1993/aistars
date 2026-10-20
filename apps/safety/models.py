"""
The safety checks: what they look for (the blocklist and the policy), and
what they found (a Screening per request, a Check per layer).
"""

from django.db import models


class Category(models.TextChoices):
    """What a request was blocked for; the user sees only this."""

    SEXUAL = "sexual", "Sexual content"
    MINORS = "minors", "Minors"
    VIOLENCE = "violence", "Violence or gore"
    HATE = "hate", "Hate or harassment"
    SELF_HARM = "self_harm", "Self-harm"
    REAL_PERSON = "real_person", "A real person"
    BRAND = "brand", "Brands or logos"
    COPYRIGHT = "copyright", "Copyrighted characters"
    ILLEGAL = "illegal", "Illegal activity"
    OTHER = "other", "Other"


class Layer(models.TextChoices):
    BLOCKLIST = "blocklist", "Blocklist"
    MODERATION = "moderation", "OpenAI Moderation"
    JUDGE = "judge", "AI policy judge"
    PROVIDER = "provider", "The provider's own check"


class Verdict(models.TextChoices):
    ALLOW = "allow", "Allowed"
    BLOCK = "block", "Blocked"
    SKIPPED = "skipped", "Skipped"
    ERROR = "error", "Could not run"


class Result(models.TextChoices):
    PASSED = "passed", "Allowed"
    BLOCKED = "blocked", "Blocked"
    UNAVAILABLE = "unavailable", "Could not be checked"


class Review(models.TextChoices):
    NONE = "", "—"
    OPEN = "open", "Waiting for review"
    CONFIRMED = "confirmed", "Block confirmed"
    OVERTURNED = "overturned", "Not a violation: strike taken back"


class BlockedTerm(models.Model):
    """A word or phrase that blocks a prompt on sight, matched as a whole word."""

    term = models.CharField(max_length=100, unique=True,
                            help_text="A word or a phrase, any language; case does not matter.")
    category = models.CharField(max_length=16, choices=Category.choices,
                                default=Category.OTHER, help_text="What the user is told.")
    active = models.BooleanField(default=True)
    chat = models.BooleanField(
        default=False,
        help_text="Also blocks chat messages and texts read aloud. Off: image requests "
                  "only (\u201cnaked eye\u201d is fine in a chat).")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("term",)
        verbose_name = "blocked word"

    def __str__(self):
        return self.term


class Policy(models.Model):
    """What the AI policy judge allows and blocks. One row."""

    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1)
    text = models.TextField(
        help_text="Read by the AI judge with every image request. Name the categories it "
                  "may answer with: sexual, minors, violence, hate, self_harm, real_person, "
                  "brand, copyright, illegal, other.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "policy"
        verbose_name_plural = "policy"

    def __str__(self):
        return "Policy"

    def save(self, *args, **kwargs):
        self.singleton = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        from .defaults import POLICY

        policy, _ = cls.objects.get_or_create(singleton=1, defaults={"text": POLICY})
        return policy


class Screening(models.Model):
    """One request through the checks: what was asked, and what came of it."""

    account = models.ForeignKey("core.Account", on_delete=models.PROTECT,
                                related_name="screenings")
    model = models.CharField(max_length=128, help_text="The AI model asked for.")
    tool = models.CharField(max_length=24, help_text="chat, image, edit or speech.")
    prompt = models.TextField()
    # The first photo; `photos` holds every one: [{"file_id", "kind"}, ...].
    photo_file_id = models.CharField(max_length=255, blank=True)
    photo_kind = models.CharField(max_length=8, blank=True)
    photos = models.JSONField(default=list, blank=True)
    result = models.CharField(max_length=12, choices=Result.choices)
    layer = models.CharField(max_length=12, choices=Layer.choices, blank=True,
                             help_text="The check that blocked it.")
    category = models.CharField(max_length=16, choices=Category.choices, blank=True)
    reason = models.CharField(max_length=500, blank=True, help_text="For the admin only.")
    strike = models.BooleanField(default=False, help_text="Counted as a strike.")
    review = models.CharField(max_length=12, choices=Review.choices, blank=True,
                              default=Review.NONE, db_index=True)
    reviewed_by = models.CharField(max_length=150, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    job_number = models.PositiveIntegerField(
        null=True, blank=True, help_text="For a request the provider refused: its number.")
    archive_message_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-id",)

    def __str__(self):
        return f"#S{self.pk} {self.get_result_display()}" + (
            f": {self.get_category_display()}" if self.category else "")


class ReviewItem(Screening):
    """The blocked requests: the review queue."""

    class Meta:
        proxy = True
        verbose_name = "blocked request"
        verbose_name_plural = "review queue"


class Check(models.Model):
    """One layer's answer for one screening."""

    screening = models.ForeignKey(Screening, on_delete=models.CASCADE, related_name="checks")
    layer = models.CharField(max_length=12, choices=Layer.choices)
    verdict = models.CharField(max_length=8, choices=Verdict.choices)
    category = models.CharField(max_length=16, choices=Category.choices, blank=True)
    detail = models.CharField(max_length=500, blank=True)
    cost_micros = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("id",)
        verbose_name = "check"

    def __str__(self):
        return f"{self.get_layer_display()}: {self.get_verdict_display()}"
