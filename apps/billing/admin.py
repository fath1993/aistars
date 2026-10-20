import csv

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.http import HttpResponse

from . import ledger, services
from .models import CreditRequest, LedgerEntry, Pack, Payment, PaymentStatus, RequestStatus


@admin.register(Pack)
class PackAdmin(admin.ModelAdmin):
    """The packs users can buy. One credit is one Star unless a pack says otherwise."""

    list_display = ("__str__", "stars", "credits", "active", "sort_order")
    list_editable = ("stars", "credits", "active", "sort_order")
    list_display_links = ("__str__",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Every Stars payment, exactly as Telegram reported it. Read-only."""

    list_display = ("created_at", "account", "stars", "credits", "status",
                    "telegram_charge_id", "refunded_by")
    list_filter = ("status", "created_at")
    search_fields = ("telegram_charge_id", "account__username",
                     "account__telegram_user_id", "account__phone")
    list_select_related = ("account",)
    actions = ("refund",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Refund — return the Stars and remove the credits")
    def refund(self, request, queryset):
        from apps.core import languages, menus
        from apps.core.services import telegram

        from . import texts

        for payment in queryset.filter(status=PaymentStatus.PAID):
            ok, note = services.refund(payment, request.user.get_username())
            self.message_user(request, note, messages.SUCCESS if ok else messages.ERROR)
            if ok:
                account = payment.account
                with languages.activate_for(account):
                    telegram.send_message(account.telegram_user_id, texts.refunded_text(
                        payment, ledger.balance(account)), reply_markup=menus.notice_keyboard())


class ApproveForm(ActionForm):
    amount = forms.IntegerField(required=False, min_value=1, label="grant",
                                help_text="empty: the amount asked for")


@admin.register(CreditRequest)
class CreditRequestAdmin(admin.ModelAdmin):
    """Users asking for credits without Stars. Approve with any amount, or reject."""

    list_display = ("created_at", "account", "phone", "country", "amount", "note",
                    "status", "granted", "decided_by")
    list_filter = ("status",)
    search_fields = ("account__username", "account__telegram_user_id", "account__phone",
                     "note")
    list_select_related = ("account", "account__country")
    readonly_fields = ("account", "amount", "note", "status", "granted", "decided_by",
                       "decided_at", "created_at")
    action_form = ApproveForm
    actions = ("approve", "reject")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="phone")
    def phone(self, obj):
        return obj.account.phone

    @admin.display(description="country")
    def country(self, obj):
        return obj.account.country or "—"

    def _tell_user(self, credit_request, text):
        from apps.core import menus
        from apps.core.services import telegram

        telegram.send_message(credit_request.account.telegram_user_id, text,
                              reply_markup=menus.notice_keyboard())

    @admin.action(description="Approve — grant the amount next to the menu, or the amount asked")
    def approve(self, request, queryset):
        from apps.core import languages

        from . import texts

        form = ApproveForm(request.POST)
        form.is_valid()
        chosen = form.cleaned_data.get("amount")
        for credit_request in queryset.filter(status=RequestStatus.PENDING):
            amount = chosen or credit_request.amount
            balance = services.approve(credit_request, amount, request.user.get_username())
            with languages.activate_for(credit_request.account):
                self._tell_user(credit_request, texts.request_approved_text(amount, balance))
            self.message_user(request, f"Granted 🪙 {amount} to {credit_request.account}.")

    @admin.action(description="Reject")
    def reject(self, request, queryset):
        from apps.core import languages

        from . import texts

        for credit_request in queryset.filter(status=RequestStatus.PENDING):
            services.reject(credit_request, request.user.get_username())
            with languages.activate_for(credit_request.account):
                self._tell_user(credit_request, texts.request_rejected_text())
            self.message_user(request, f"Rejected the request of {credit_request.account}.")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    """Every credit movement. Nothing here can be edited or deleted."""

    list_display = ("created_at", "account", "kind", "amount", "balance_after",
                    "created_by", "note")
    list_filter = ("kind", "created_at")
    search_fields = ("account__username", "account__telegram_user_id", "account__phone",
                     "note", "created_by")
    list_select_related = ("account",)
    # The ledger only grows: no date_hierarchy (a scan of every row per page,
    # see ActivityLogAdmin) and no second count of the whole table.
    show_full_result_count = False
    actions = ("export_csv",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Export the selected rows as CSV")
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="ledger.csv"'
        writer = csv.writer(response)
        writer.writerow(["id", "created_at", "telegram_user_id", "account", "kind", "amount",
                         "balance_after", "payment", "request", "created_by", "note"])
        for entry in queryset.select_related("account").order_by("id"):
            writer.writerow([entry.pk, entry.created_at.isoformat(),
                             entry.account.telegram_user_id, str(entry.account), entry.kind,
                             entry.amount, entry.balance_after, entry.payment_id or "",
                             entry.request_id or "", entry.created_by, entry.note])
        return response
