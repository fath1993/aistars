from django import forms
from django.contrib import admin, messages

from . import clients, sync
from .models import AIModel, Provider


class ProviderForm(forms.ModelForm):
    # Never sent back to the browser; an empty field keeps the saved key.
    api_key = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Paste a new key to replace the saved one. Empty keeps it.")

    class Meta:
        model = Provider
        fields = ("kind", "api_key", "base_url", "enabled")

    def clean_api_key(self):
        key = self.cleaned_data["api_key"].strip()
        return key or (self.instance.api_key if self.instance.pk else "")


class ModelInline(admin.TabularInline):
    model = AIModel
    extra = 0
    fields = ("model_id", "label", "use", "enabled", "offered", "is_default", "listed")
    readonly_fields = ("listed",)
    show_change_link = True


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    form = ProviderForm
    list_display = ("__str__", "key", "enabled", "models_count", "synced_at", "updated_at")
    readonly_fields = ("synced_at",)
    inlines = (ModelInline,)
    actions = ("test", "sync_models")

    @admin.display(description="API key")
    def key(self, obj):
        return obj.masked_key()

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
