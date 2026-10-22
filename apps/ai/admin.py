from django import forms
from django.contrib import admin, messages

from . import adapters, clients, sync
from .models import AICall, AIModel, Provider, Use


class ProviderForm(forms.ModelForm):
    # Never sent back to the browser; an empty field keeps the saved key.
    api_key = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Paste a new key to replace the saved one. Empty keeps it.")
    admin_key = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Optional, OpenAI only: an Admin key (platform.openai.com → Settings → "
                  "Admin keys), so My finances shows OpenAI's own cost figures. Empty keeps "
                  "the saved one.")

    class Meta:
        model = Provider
        fields = ("kind", "api_key", "admin_key", "base_url", "enabled")

    def clean_api_key(self):
        key = self.cleaned_data["api_key"].strip()
        return key or (self.instance.api_key if self.instance.pk else "")

    def clean_admin_key(self):
        key = self.cleaned_data["admin_key"].strip()
        return key or (self.instance.admin_key if self.instance.pk else "")


class ModelInline(admin.TabularInline):
    model = AIModel
    extra = 0
    fields = ("model_id", "label", "use", "enabled", "offered", "is_default", "listed")
    readonly_fields = ("listed",)
    show_change_link = True


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    form = ProviderForm
    list_display = ("__str__", "key", "enabled", "does", "offers", "models_count",
                    "synced_at", "updated_at")
    readonly_fields = ("synced_at",)
    inlines = (ModelInline,)
    actions = ("test", "sync_models")

    @admin.display(description="API key")
    def key(self, obj):
        return obj.masked_key()

    @admin.display(description="can do")
    def does(self, obj):
        names = dict(Use.choices)
        functions = getattr(adapters.ADAPTERS.get(obj.kind), "functions", ())
        return ", ".join(names[use] for use in Use.values if use in functions) or "—"

    @admin.display(description="offers users now")
    def offers(self, obj):
        from apps.core.models import ControlPanel

        names = dict(Use.choices)
        return ", ".join(names[use] for use in obj.offers(ControlPanel.get().demo_mode)) \
            or "nothing"

    @admin.display(description="models (offered)")
    def models_count(self, obj):
        rows = list(obj.models.all())
        return f"{len(rows)} ({sum(1 for model in rows if model.offered)})"

    @admin.action(description="Test — make one tiny call with the saved key")
    def test(self, request, queryset):
        for provider in queryset:
            ok, note = clients.test_provider(provider)
            self.message_user(request, note, messages.SUCCESS if ok else messages.ERROR)

    @admin.action(description="Sync models — read the provider's list of models")
    def sync_models(self, request, queryset):
        for provider in queryset:
            try:
                result = sync.sync(provider)
            except clients.AIError as exc:
                self.message_user(request, f"{provider}: {exc}", messages.ERROR)
                continue
            added = ", ".join(result["added"]) or "none"
            self.message_user(
                request, f"{provider}: {result['listed']} model(s) listed. New, added "
                         f"switched off (set their prices, then offer them): {added}.",
                messages.SUCCESS)
            if result["missing"]:
                self.message_user(
                    request, f"{provider} does not list: {', '.join(result['missing'])}. "
                             "Check their IDs, or switch them off.", messages.WARNING)


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ("model_id", "label", "provider", "use", "enabled", "offered",
                    "is_default", "price", "listed", "ready_for_users")
    list_editable = ("enabled", "offered", "is_default")
    list_filter = ("use", "provider", "offered", "enabled", "listed")
    list_select_related = ("provider",)
    search_fields = ("model_id", "label")
    readonly_fields = ("listed",)
    fieldsets = (
        (None, {"fields": ("provider", "model_id", "label", "use", "tagline", "sees_images",
                           "enabled", "offered", "is_default", "sort_order", "listed")}),
        ("Prices (USD)", {
            "description": "Chat: input and output per 1M tokens. Images: input (prompt), "
                           "image input (photos sent in) and output (the image) per 1M "
                           "tokens. Voice to text: unit price per minute. Text to speech: "
                           "unit price per 1M characters.",
            "fields": ("input_price", "output_price", "image_input_price", "unit_price",
                       "cache_write_factor", "cache_read_factor", "max_output_tokens",
                       "margin")}),
        ("Used by the bot itself", {"fields": ("default_for_translation",
                                               "default_for_safety")}),
    )

    @admin.display(description="price")
    def price(self, obj):
        if obj.use in ("transcription",):
            return f"${obj.unit_price}/min"
        if obj.use == "speech":
            return f"${obj.unit_price}/1M chars"
        return f"${obj.input_price} / ${obj.output_price}"

    @admin.display(description="users can have it", boolean=True)
    def ready_for_users(self, obj):
        return obj.ready


@admin.register(AICall)
class AICallAdmin(admin.ModelAdmin):
    """Every call to a provider and what it cost: the owner's AI costs, call by call."""

    list_display = ("at", "provider", "model", "purpose", "cost_usd", "input_tokens",
                    "cached_tokens", "output_tokens")
    list_filter = ("provider", "purpose", "at")
    search_fields = ("model", "note")
    show_full_result_count = False
    readonly_fields = [field.name for field in AICall._meta.fields]

    @admin.display(description="cost (USD)", ordering="cost_micros")
    def cost_usd(self, obj):
        return f"{obj.cost_micros / 1e6:.4f}"

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
