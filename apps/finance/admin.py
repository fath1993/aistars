"""
💼 My finances, for superusers only: the money overview (any month, or all
time, with a spreadsheet for the accountant), the bills and their invoice
files, and the bot's Telegram Stars.
"""

import csv
from datetime import datetime

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.billing.pricing import icon

from . import money
from .models import Bill, Overview, StarsAccount


class OwnerOnly:
    """Money is the owner's: superusers only, whatever other rights staff have."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(Bill)
class BillAdmin(OwnerOnly, admin.ModelAdmin):
    list_display = ("date", "payee", "description", "amount", "currency", "usd", "paid",
                    "invoice_link", "repeats_monthly")
    list_filter = ("payee", "paid", "currency", "date")
    search_fields = ("description", "invoice_number")
    list_editable = ("paid",)
    fields = ("date", "payee", "description", "amount", "currency", "invoice_number",
              "invoice", "paid", "repeats_monthly")
    actions = ("mark_paid",)

    @admin.display(description="USD")
    def usd(self, obj):
        return f"{money.bill_usd(obj):.2f}"

    @admin.display(description="invoice")
    def invoice_link(self, obj):
        if not obj.invoice:
            return "—"
        url = reverse("admin:finance_bill_invoice", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">open</a>', url)

    @admin.action(description="Mark as paid")
    def mark_paid(self, request, queryset):
        count = queryset.update(paid=True)
        self.message_user(request, f"{count} bill(s) marked as paid.", messages.SUCCESS)

    def get_urls(self):
        return [path("<int:pk>/invoice/", self.admin_site.admin_view(self.invoice_view),
                     name="finance_bill_invoice")] + super().get_urls()

    def invoice_view(self, request, pk):
        """The invoice file, to superusers only: it is never served publicly."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        bill = Bill.objects.filter(pk=pk).first()
        if bill is None or not bill.invoice:
            raise Http404("No invoice file")
        try:
            handle = bill.invoice.open("rb")
        except FileNotFoundError:
            raise Http404("The invoice file is missing on the server") from None
        return FileResponse(handle, filename=bill.invoice.name.rsplit("/", 1)[-1])


@admin.register(Overview)
class OverviewAdmin(OwnerOnly, admin.ModelAdmin):
    """The money page: in, out, profit, what is left at the providers, what users hold."""

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        chosen = request.GET.get("month", "")
        start, end, label = _period(chosen)
        if request.GET.get("format") == "csv":
            return _csv(start, end, label)
        data = money.overview(start, end)
        context = {
            **self.admin_site.each_context(request),
            "title": f"My finances · {label}", "label": label, "chosen": chosen,
            "months": [(f"{year}-{month:02d}", datetime(year, month, 1).strftime("%B %Y"))
                       for year, month in money.months()],
            "data": data, "days": money.daily(start, end) if chosen != "all" else [],
            "balances": money.provider_balances(), "held": money.held_credits(),
            "billed": money.providers_billed(start, end),
            "eur": {key: money.in_eur(value) for key, value in (
                ("net", data["income"]["net"]), ("costs", data["costs"]),
                ("profit", data["profit"]))},
            "eur_rate": money.eur_rate(), "star_value": money.star_value(),
            "credit_icon": icon(),
        }
        return TemplateResponse(request, "admin/finance/overview.html", context)


@admin.register(StarsAccount)
class StarsAdmin(OwnerOnly, admin.ModelAdmin):
    """The bot's Stars, as Telegram counts them: the balance and the transactions."""

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        from apps.core.services import telegram

        if not self.has_view_permission(request):
            raise PermissionDenied
        balance = telegram.star_balance()
        stars = balance.get("amount") if isinstance(balance, dict) else None
        transactions = telegram.star_transactions() or []
        context = {
            **self.admin_site.each_context(request),
            "title": "Telegram Stars", "stars": stars,
            "stars_usd": money.stars_usd(stars) if stars is not None else None,
            "rows": [_transaction(row) for row in transactions],
            "star_value": money.star_value(),
        }
        return TemplateResponse(request, "admin/finance/stars.html", context)


def _period(chosen) -> tuple:
    """(start, end, label) of ?month=2026-10, ?month=all, or this month."""
    if chosen == "all":
        start, end = money.all_time()
        return start, end, "All time"
    try:
        year, month = (int(part) for part in chosen.split("-"))
        start, end = money.month_range(year, month)
    except (ValueError, TypeError):
        today = timezone.localdate()
        year, month = today.year, today.month
        start, end = money.month_range(year, month)
    return start, end, datetime(year, month, 1).strftime("%B %Y")


def _csv(start, end, label):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    name = "all-time" if label == "All time" else start.strftime("%Y-%m")
    response["Content-Disposition"] = f'attachment; filename="aistars-finances-{name}.csv"'
    writer = csv.writer(response)
    for row in money.csv_rows(start, end):
        writer.writerow(row)
    return response


def _transaction(row) -> dict:
    """One Stars transaction: in or out, how many, and what it was."""
    source, receiver = row.get("source") or {}, row.get("receiver") or {}
    incoming = bool(source)
    partner = source if incoming else receiver
    kind = partner.get("type", "")
    user = partner.get("user") or {}
    who = user.get("username") and f"@{user['username']}" or user.get("id", "")
    what = {
        "user": f"Payment from {who}" if incoming else f"Refund to {who}",
        "fragment": "Withdrawal (Fragment)",
        "telegram_ads": "Telegram Ads",
        "telegram_api": "Telegram API (paid broadcasts)",
    }.get(kind, kind or "—")
    if kind == "fragment":
        state = (partner.get("withdrawal_state") or {}).get("type", "")
        what += f": {state}" if state else ""
    stars = int(row.get("amount") or 0)
    return {"date": datetime.fromtimestamp(int(row.get("date") or 0),
                                           tz=timezone.get_current_timezone()),
            "incoming": incoming, "stars": stars if incoming else -stars,
            "usd": money.stars_usd(stars if incoming else -stars), "what": what,
            "id": row.get("id", "")}
