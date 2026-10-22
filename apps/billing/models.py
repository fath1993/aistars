"""
Money in the bot. One credit is one Telegram Star.

The ledger is append-only: every credit movement is one row that is never
edited or deleted, and a correction is a new row. A user's balance is the
balance_after of their newest row.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .pricing import icon


class Pack(models.Model):
    """A credit pack users can buy with Stars."""

    # Telegram's limits for one Stars invoice: 1 to 10,000.
    stars = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10_000)],
        help_text="Price in Telegram Stars (1 to 10000).")
    credits = models.PositiveIntegerField(validators=[MinValueValidator(1)],
                                          help_text="Credits the user receives.")
    active = models.BooleanField(default=True, help_text="Offered in the bot.")
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "stars")

    def __str__(self):
        return f"{icon()} {self.credits} = ⭐ {self.stars}"


class PaymentStatus(models.TextChoices):
    PAID = "paid", "Paid"
    REFUNDED = "refunded", "Refunded"


class Payment(models.Model):
    """One Stars payment, exactly as Telegram reported it."""

    account = models.ForeignKey("core.Account", on_delete=models.PROTECT,
                                related_name="payments")
    pack = models.ForeignKey(Pack, null=True, blank=True, on_delete=models.SET_NULL)
    stars = models.PositiveIntegerField()
    credits = models.PositiveIntegerField()
    # Unique: Telegram may deliver the same payment twice; it counts once.
    telegram_charge_id = models.CharField(max_length=128, unique=True)
    provider_charge_id = models.CharField(max_length=128, blank=True)
    payload = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=8, choices=PaymentStatus.choices,
                              default=PaymentStatus.PAID, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    refunded_by = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"⭐ {self.stars} from {self.account} ({self.get_status_display()})"


class RequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class CreditRequest(models.Model):
    """A user asking for credits without paying with Stars."""

    account = models.ForeignKey("core.Account", on_delete=models.PROTECT,
                                related_name="credit_requests")
    amount = models.PositiveIntegerField(help_text="Credits asked for.")
    note = models.TextField(blank=True)
    status = models.CharField(max_length=8, choices=RequestStatus.choices,
                              default=RequestStatus.PENDING, db_index=True)
    granted = models.PositiveIntegerField(null=True, blank=True)
    decided_by = models.CharField(max_length=150, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.account}: {icon()} {self.amount} ({self.get_status_display()})"


class EntryKind(models.TextChoices):
    PURCHASE = "purchase", "Purchase"
    MANUAL_GRANT = "manual_grant", "Credit request approved"
    ADMIN_ADJUST = "admin_adjust", "Admin adjustment"
    REFUND = "refund", "Payment refunded"
    WELCOME = "welcome", "Welcome credits"
    # A request (a chat answer, an image, a voice note): the most it can cost
    # is held when it starts (hold, −most); when it is done, what it really
    # cost is charged and the rest of the hold goes back (charge, +the rest);
    # when it fails, all of it goes back (return, +most). Each at most once
    # per request.
    HOLD = "hold", "Held for a request"
    CHARGE = "charge", "Request done: charged, the rest returned"
    RETURN = "return", "Request not done: hold returned"


HOLD_KINDS = (EntryKind.HOLD, EntryKind.CHARGE, EntryKind.RETURN)


class LedgerEntry(models.Model):
    """One credit movement. Never edited, never deleted."""

    account = models.ForeignKey("core.Account", on_delete=models.PROTECT,
                                related_name="ledger")
    kind = models.CharField(max_length=16, choices=EntryKind.choices, db_index=True)
    amount = models.IntegerField(help_text="Credits in (+) or out (−).")
    balance_after = models.IntegerField()
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="entries")
    request = models.ForeignKey(CreditRequest, null=True, blank=True,
                                on_delete=models.PROTECT, related_name="entries")
    job = models.ForeignKey("studio.Job", null=True, blank=True,
                            on_delete=models.PROTECT, related_name="entries")
    note = models.CharField(max_length=255, blank=True,
                            help_text="For the admin; never shown to the user.")
    created_by = models.CharField(max_length=150, default="system")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-id",)
        indexes = [models.Index(fields=["account", "-id"])]
        constraints = [
            # A request is held, charged and returned at most once each.
            models.UniqueConstraint(fields=["job", "kind"],
                                    condition=models.Q(job__isnull=False),
                                    name="one_movement_per_kind_per_job"),
        ]
        verbose_name = "ledger entry"
        verbose_name_plural = "ledger"

    def __str__(self):
        return f"{self.account}: {self.amount:+d} → {self.balance_after}"
