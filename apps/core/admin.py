from django import forms
from django.contrib import admin
from django.contrib.admin.helpers import ActionForm

from apps.ai.models import AIModel

from . import activity, i18n, languages, runtime, translation
from .models import (
    Account,
    AccountStatus,
    ActivityLog,
    ControlPanel,
    Country,
    Language,
    LogCategory,
    RuntimeSetting,
    SourceText,
    SpendLedger,
    TelegramUpdate,
    Translation,
    TranslationStatus,
)


def fixed_choices(model_admin, request, name, queryset=None):
    """
    The formfield_callback of a changelist whose list_editable has a select:
    its options are loaded once per page. Left alone, every row's copy of the
    field runs its own query (245 on the countries) and lists every option.
    """
    def callback(db_field, **kwargs):
        field = model_admin.formfield_for_dbfield(db_field, request=request, **kwargs)
        if db_field.name == name and field is not None:
            if queryset is not None:
                field.queryset = queryset
            field.choices = list(field.choices)
            # No change/view icons on every row: the page is a list of choices.
            for flag in ("can_add_related", "can_change_related", "can_view_related",
                         "can_delete_related"):
                if hasattr(field.widget, flag):
                    setattr(field.widget, flag, False)
        return field
    return callback


class AdjustCreditForm(ActionForm):
    """Options next to the action menu, read by the Adjust credit action."""

    amount = forms.IntegerField(required=False, label="credits (+/−)")
    reason = forms.CharField(required=False, max_length=200, label="reason")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("telegram_user_id", "display_name", "username", "status",
                    "credits", "country", "phone", "bot_language", "tester", "strikes",
                    "registered_at", "last_seen_at")
    action_form = AdjustCreditForm
    list_filter = ("status", "tester", ("registered_at", admin.EmptyFieldListFilter),
                   "country")
    search_fields = ("telegram_user_id", "username", "display_name", "phone")
    readonly_fields = ("telegram_user_id", "username", "display_name",
                       "language_code", "phone", "country", "registered_at",
                       "last_seen_at", "ui_message_id", "created_at", "updated_at",
                       "telegram_updates")

    @admin.display(description="Telegram updates")
    def telegram_updates(self, obj):
        """What this user sent the bot, as it came."""
        from django.urls import reverse
        from django.utils.html import format_html

        url = reverse("admin:core_telegramupdate_changelist") + f"?user_id={obj.telegram_user_id}"
        return format_html('<a href="{}">Everything this user sent</a>', url)

    def get_queryset(self, request):
        from django.db.models import OuterRef, Subquery

        from apps.billing.models import LedgerEntry

        newest = LedgerEntry.objects.filter(account=OuterRef("pk")).order_by("-id")
        return super().get_queryset(request).select_related("country").annotate(
            balance=Subquery(newest.values("balance_after")[:1]))

    @admin.display(description="credits", ordering="balance")
    def credits(self, obj):
        return f"🪙 {obj.balance or 0}"

    def changelist_view(self, request, extra_context=None):
        # Loaded once per page, not once per row.
        self._used = languages.used_codes()
        self._names = dict(Language.objects.values_list("code", "native_name"))
        return super().changelist_view(request, extra_context)

    @admin.display(description="language")
    def bot_language(self, obj):
        used = getattr(self, "_used", None)
        code = languages.resolve(obj, used)
        name = getattr(self, "_names", {}).get(code) or languages.name(code)
        return f"{name}{'' if obj.language else ' (automatic)'}"
    actions = ("block", "unblock", "adjust_credit", "make_tester", "unmake_tester")

    @admin.action(description="Adjust credit — add or remove the credits next to the menu")
    def adjust_credit(self, request, queryset):
        from django.contrib import messages

        from apps.billing import ledger, services

        form = AdjustCreditForm(request.POST)
        form.is_valid()
        amount = form.cleaned_data.get("amount") or 0
        reason = (form.cleaned_data.get("reason") or "").strip()
        if not amount or not reason:
            self.message_user(request, "Give the credits (+ or −) and a reason next to the "
                              "action menu.", messages.ERROR)
            return
        for account in queryset:
            try:
                balance = services.adjust(account, amount, reason,
                                          request.user.get_username())
            except ledger.InsufficientCredits as exc:
                self.message_user(request, f"{account}: {exc}", messages.ERROR)
                continue
            self.message_user(request, f"{account}: {amount:+d}, balance 🪙 {balance}.")

    def _set_status(self, request, queryset, status, event):
        # Listed before the update: a changelist filtered by status would
        # otherwise re-evaluate to nothing once the status has changed.
        accounts = list(queryset)
        count = queryset.update(status=status)
        for account in accounts:
            activity.info(LogCategory.ADMIN, event,
                          f"{account} set to {status} by {request.user}",
                          account=account)
        self.message_user(request, f"{count} account(s) updated.")

    @admin.action(description="Block — stop the account using the bot")
    def block(self, request, queryset):
        self._set_status(request, queryset, AccountStatus.BLOCKED, "blocked")

    @admin.action(description="Unblock")
    def unblock(self, request, queryset):
        self._set_status(request, queryset, AccountStatus.ACTIVE, "unblocked")

    @admin.action(description="Tester — sees the AI tools while they are tested")
    def make_tester(self, request, queryset):
        self._set_tester(request, queryset, True)

    @admin.action(description="Not a tester")
    def unmake_tester(self, request, queryset):
        self._set_tester(request, queryset, False)

    def _set_tester(self, request, queryset, tester):
        accounts = list(queryset)
        count = queryset.update(tester=tester)
        for account in accounts:
            activity.info(LogCategory.ADMIN, "tester_set" if tester else "tester_unset",
                          f"{account} {'made' if tester else 'no longer'} a tester by "
                          f"{request.user}", account=account)
        self.message_user(request, f"{count} account(s) updated.")


class TranslateActionForm(ActionForm):
    """Options next to the action menu, read by the retranslate action."""

    model = forms.ModelChoiceField(
        queryset=AIModel.objects.filter(enabled=True), required=False,
        empty_label="default model", label="with")
    overwrite_manual = forms.BooleanField(required=False,
                                          label="also replace hand-made fixes")


class TranslationInline(admin.TabularInline):
    """Every text of the bot in this language. Edit one to fix it by hand."""

    model = Translation
    fields = ("english", "text", "manual", "model", "updated_at")
    readonly_fields = ("english", "model", "updated_at")
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("source").filter(
            source__active=True)

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="English")
    def english(self, obj):
        return obj.source.text


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    """
    Every language of the world. Used: offered to users. Translate: kept
    translated by AI in the background. Open one to see or fix its texts.
    """

    list_display = ("name", "native_name", "code", "used", "translate", "progress",
                    "status_info", "translator", "translated_at")
    list_editable = ("used", "translate", "translator")
    list_filter = ("used", "translate", "status")
    search_fields = ("name", "native_name", "code")
    ordering = ("-used", "-translate", "name")
    # Every row is a small form; 186 of them took half a second to draw. The
    # used and translated languages come first; search finds the rest.
    list_per_page = 100
    action_form = TranslateActionForm
    actions = ("translate_missing", "retranslate")
    fields = ("code", "name", "native_name", "used", "translate", "translator",
              "status", "status_detail", "translated_at")
    readonly_fields = ("code", "name", "native_name", "status", "status_detail",
                       "translated_at")
    inlines = (TranslationInline,)

    def get_queryset(self, request):
        from django.db.models import Count, Q, Value

        total = SourceText.objects.filter(active=True).count()
        return super().get_queryset(request).select_related("translator").annotate(
            done=Count("translations", filter=Q(translations__source__active=True)),
            total=Value(total))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_changelist_formset(self, request, **kwargs):
        return super().get_changelist_formset(
            request, formfield_callback=fixed_choices(self, request, "translator"), **kwargs)

    @admin.display(description="translated", ordering="done")
    def progress(self, obj):
        if obj.code == i18n.SOURCE:
            return "source"
        if not obj.total:
            return "—"
        return f"{obj.done * 100 // obj.total}% ({obj.done}/{obj.total})"

    @admin.display(description="status", ordering="status")
    def status_info(self, obj):
        if obj.status == TranslationStatus.IDLE:
            return "—"
        return f"{obj.get_status_display()}: {obj.status_detail}" if obj.status_detail \
            else obj.get_status_display()

    def save_model(self, request, obj, form, change):
        was_translating = (change and Language.objects.filter(
            pk=obj.pk, translate=True).exists())
        if obj.code == i18n.SOURCE:
            obj.translate = False   # English is the source: nothing to translate
        super().save_model(request, obj, form, change)
        if obj.translate and not was_translating:
            translation.queue(obj.code)
            self.message_user(request, f"{obj.name}: translation started in the background.")

    def save_formset(self, request, form, formset, change):
        # A text changed here is a hand-made fix: retranslating keeps it.
        formset.save(commit=False)
        for removed in formset.deleted_objects:
            removed.delete()
        for row in formset.forms:
            if row in formset.deleted_forms or not row.has_changed():
                continue
            if "text" in row.changed_data:
                row.instance.manual = True
                row.instance.model = ""
            row.instance.save()

    def _queue(self, request, queryset, **options):
        names = []
        for language in queryset.exclude(code=i18n.SOURCE):
            translation.queue(language.code, **options)
            names.append(language.name)
        self.message_user(request, f"Started in the background: {', '.join(names) or 'nothing'}.")

    @admin.action(description="Translate missing texts now")
    def translate_missing(self, request, queryset):
        self._queue(request, queryset)

    @admin.action(description="Retranslate everything (choose the model next to the menu)")
    def retranslate(self, request, queryset):
        form = TranslateActionForm(request.POST)
        form.is_valid()
        model = form.cleaned_data.get("model")
        self._queue(request, queryset, model_id=model.pk if model else None, overwrite=True,
                    overwrite_manual=bool(form.cleaned_data.get("overwrite_manual")))


@admin.register(Translation)
class TranslationAdmin(admin.ModelAdmin):
    """Every translation, searchable across languages. Editing one makes it manual."""

    list_display = ("language", "english", "text", "manual", "model", "updated_at")
    list_filter = ("manual", ("language", admin.RelatedOnlyFieldListFilter))
    search_fields = ("text", "source__text")
    list_select_related = ("language", "source")
    fields = ("language", "english", "text", "manual", "model", "updated_at")
    readonly_fields = ("language", "english", "model", "updated_at")

    def has_add_permission(self, request):
        return False

    @admin.display(description="English")
    def english(self, obj):
        return obj.source.text

    def save_model(self, request, obj, form, change):
        if "text" in form.changed_data:
            obj.manual = True
            obj.model = ""
        super().save_model(request, obj, form, change)


@admin.register(SourceText)
class SourceTextAdmin(admin.ModelAdmin):
    """The bot's English texts, as found in the code on the last deploy."""

    list_display = ("text", "location", "active", "first_seen")
    list_filter = ("active",)
    search_fields = ("text", "location")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    """Who may use the bot, by the country of their phone number."""

    list_display = ("name", "flag", "code", "calling", "allowed", "default_language",
                    "users")
    list_editable = ("allowed", "default_language")
    list_filter = ("allowed", "default_language")
    search_fields = ("name", "code", "calling_code")
    fields = ("code", "name", "calling_code", "allowed", "default_language")
    readonly_fields = ("code", "name", "calling_code")
    actions = ("allow", "disallow")
    list_per_page = 100   # each row is a small form; search and the filters find the rest

    def get_queryset(self, request):
        from django.db.models import Count

        return super().get_queryset(request).annotate(user_count=Count("accounts"))

    def get_changelist_formset(self, request, **kwargs):
        # In the list, only the languages users can get (a default that is not
        # used falls through anyway) and those already chosen, so no row loses
        # its value: 186 options on 245 rows was 2 MB. The country's own page
        # still offers every language.
        from django.db.models import Q

        offered = Language.objects.filter(Q(used=True) | Q(country__isnull=False)).distinct()
        return super().get_changelist_formset(
            request, formfield_callback=fixed_choices(self, request, "default_language",
                                                      offered), **kwargs)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="calling code")
    def calling(self, obj):
        return f"+{obj.calling_code}"

    @admin.display(description="users", ordering="user_count")
    def users(self, obj):
        return obj.user_count

    def _set_allowed(self, request, queryset, allowed):
        codes = sorted(queryset.values_list("code", flat=True))
        count = queryset.update(allowed=allowed)
        activity.info(LogCategory.ADMIN, "countries_allowed" if allowed else "countries_disallowed",
                      f"{', '.join(codes)} {'allowed' if allowed else 'disallowed'} by {request.user}")
        self.message_user(request, f"{count} countr{'y' if count == 1 else 'ies'} updated.")

    @admin.action(description="Allow — users from these countries can use the bot")
    def allow(self, request, queryset):
        self._set_allowed(request, queryset, True)

    @admin.action(description="Disallow — their users are refused from their next message")
    def disallow(self, request, queryset):
        self._set_allowed(request, queryset, False)


@admin.register(RuntimeSetting)
class RuntimeSettingAdmin(admin.ModelAdmin):
    """Every value that can change during the application's life."""

    list_display = ("key", "group", "label", "current", "source")
    list_filter = ("group", "is_secret")
    search_fields = ("key", "label")
    readonly_fields = ("key", "group", "label", "kind", "is_secret",
                       "help_text", "default_value", "source")
    fields = ("key", "label", "group", "kind", "value", "help_text",
              "default_value", "is_secret", "source")

    @admin.display(description="value")
    def current(self, obj):
        return obj.masked()

    @admin.display(description="in effect")
    def source(self, obj):
        """Which layer actually wins, so a blank row is not mistaken for unset."""
        if obj.value:
            return "this row"
        from django.conf import settings as dj

        if getattr(dj, obj.key, None) not in (None, ""):
            return "falling back to .env / settings"
        return "registry default"

    def has_add_permission(self, request):
        # Rows come from the registry; a hand-made key would never be read.
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ControlPanel)
class ControlPanelAdmin(admin.ModelAdmin):
    # Adds a "System status & webhook" button to the changelist.
    change_list_template = "admin/core/controlpanel/change_list.html"

    list_display = ("__str__", "demo_mode", "open_to_everyone", "paused", "daily_budget",
                    "hourly_budget", "spent_today")
    readonly_fields = ("spent_today", "spent_this_hour", "paused_at")

    def get_urls(self):
        from django.urls import path

        return [
            path("diagnostics/", self.admin_site.admin_view(self.diagnostics_view),
                 name="core_diagnostics"),
        ] + super().get_urls()

    def diagnostics_view(self, request):
        """The doctor, in the admin: same checks, same fixes, no shell."""
        from django.contrib import messages
        from django.shortcuts import redirect
        from django.template.response import TemplateResponse

        from . import diagnostics

        if request.method == "POST":
            action = request.POST.get("action")
            if action == "register_webhook":
                ok, note = diagnostics.register_webhook()
                (messages.success if ok else messages.error)(request, note)
            elif action == "resume":
                ok, note = diagnostics.resume_platform()
                (messages.success if ok else messages.error)(request, note)
            return redirect(request.path)

        report = diagnostics.run()
        context = {
            **self.admin_site.each_context(request),
            "title": "System status",
            "report": report,
            "sections": report.sections(),
            "webhook_url": runtime.webhook_url(),
        }
        return TemplateResponse(request, "admin/core/diagnostics.html", context)

    @admin.display(description="spent today")
    def spent_today(self, obj):
        from .services import budget

        row = budget.spent_today()
        cap = obj.daily_cap_micros()
        return f"{row['cost'] / 1e6:.4f} of {cap / 1e6:.2f} USD ({row['calls']} calls)"

    @admin.display(description="spent this hour")
    def spent_this_hour(self, obj):
        from .services import budget

        row = budget.spent_this_hour()
        return f"{row['cost'] / 1e6:.4f} USD ({row['calls']} calls)"

    # Single row by design.
    def has_add_permission(self, request):
        return not ControlPanel.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SpendLedger)
class SpendLedgerAdmin(admin.ModelAdmin):
    list_display = ("date", "hour", "calls", "cost_display")
    list_filter = ("date",)

    @admin.display(description="cost (USD)")
    def cost_display(self, obj):
        return f"{obj.cost_micros / 1_000_000:.4f}"

    # Append-only: editing it by hand would make the guard lie.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    """Append-only history of what the application did."""

    list_display = ("created_at", "level", "category", "event", "short_message",
                    "account", "duration_ms")
    list_filter = ("level", "category", "created_at")
    search_fields = ("event", "message")
    # No date_hierarchy: its year/month/day links read the whole table on
    # every page. The created_at filter (today, 7 days, month) uses the index.
    show_full_result_count = False
    readonly_fields = tuple(f.name for f in ActivityLog._meta.fields)
    raw_id_fields = ("account",)

    @admin.display(description="message")
    def short_message(self, obj):
        return (obj.message or "")[:110]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TelegramUpdate)
class TelegramUpdateAdmin(admin.ModelAdmin):
    """
    Every update Telegram sent, as it came: what a user really sent, whether
    Telegram sent anything at all (an update turned away for a wrong secret is
    listed as Rejected), and why one failed.
    """

    list_display = ("received_at", "update_id", "kind", "sender", "summary", "outcome",
                    "deliveries")
    list_filter = ("outcome", "kind", "received_at")
    search_fields = ("=update_id", "=user_id", "summary")
    # Large and growing: no full count, no date drill-down (see the activity log).
    show_full_result_count = False
    fields = ("update_id", "kind", "sender", "summary", "outcome", "deliveries",
              "received_at", "handled_at", "error", "pretty_payload")
    readonly_fields = fields

    def get_queryset(self, request):
        from django.db.models import OuterRef, Subquery

        account = Account.objects.filter(telegram_user_id=OuterRef("user_id")).values("pk")[:1]
        return super().get_queryset(request).annotate(account_pk=Subquery(account))

    @admin.display(description="from")
    def sender(self, obj):
        """The account, when the update came from a user the bot knows."""
        from django.urls import reverse
        from django.utils.html import format_html

        if obj.user_id is None:
            return "—"
        if not obj.account_pk:
            return obj.user_id
        url = reverse("admin:core_account_change", args=[obj.account_pk])
        return format_html('<a href="{}">{}</a>', url, obj.user_id)

    @admin.display(description="payload")
    def pretty_payload(self, obj):
        import json

        from django.utils.html import format_html

        return format_html("<pre>{}</pre>", json.dumps(obj.payload, indent=2,
                                                        ensure_ascii=False))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
