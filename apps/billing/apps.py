from django.apps import AppConfig


class BillingConfig(AppConfig):
    """Credits: packs bought with Telegram Stars, the ledger, credit requests."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"
    verbose_name = "Billing"
