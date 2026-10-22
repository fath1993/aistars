"""
The owner's bills: what was paid to the AI providers (to keep track of what
is left there) and every other cost (server, domain…), with the invoice
file. Money in (Stars) and the AI costs call by call are recorded elsewhere
(billing.Payment, ai.AICall); money.py puts them together.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Payee(models.TextChoices):
    OPENAI = "openai", "OpenAI"
    ANTHROPIC = "anthropic", "Anthropic"
    SERVER = "server", "Server"
    DOMAIN = "domain", "Domain"
    OTHER = "other", "Other"


# Paid to an AI provider: what the bot used there is counted call by call
# (AICall), so these count toward the balance left there, not as costs.
PROVIDERS = (Payee.OPENAI, Payee.ANTHROPIC)


class Currency(models.TextChoices):
    USD = "USD", "USD"
    EUR = "EUR", "EUR"


class Bill(models.Model):
    date = models.DateField(default=timezone.localdate, db_index=True)
    payee = models.CharField(max_length=16, choices=Payee.choices,
                             help_text="OpenAI and Anthropic: a payment for AI credit (a "
                                       "top-up or a monthly invoice). It counts toward the "
                                       "balance left there; the AI itself is counted call by "
                                       "call.")
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.USD)
    invoice_number = models.CharField(max_length=64, blank=True)
    invoice = models.FileField(upload_to="invoices/%Y/%m/", blank=True,
                               help_text="The invoice file (PDF or image). Only admins can "
                                         "open it.")
    paid = models.BooleanField(default=True)
    repeats_monthly = models.BooleanField(
        default=False, help_text="A copy is added on the same day every month, unpaid, ready "
                                 "for its invoice (the server, a subscription).")
    repeat_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="repeats", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date", "-id")
        verbose_name = "bill"
        verbose_name_plural = "bills & invoices"

    def __str__(self):
        return f"{self.date} {self.get_payee_display()} {self.amount} {self.currency}"

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "More than 0."})


class Overview(Bill):
    """The money page (My finances → Overview): no table of its own."""

    class Meta:
        proxy = True
        verbose_name = "overview"
        verbose_name_plural = "overview"


class StarsAccount(Bill):
    """The Telegram Stars page (My finances → Telegram Stars)."""

    class Meta:
        proxy = True
        verbose_name = "Telegram Stars"
        verbose_name_plural = "Telegram Stars"
