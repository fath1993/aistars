from django.contrib import admin, messages

from . import jobs
from .models import ACTIVE, Assistant, Conversation, Job, Message


@admin.register(Assistant)
class AssistantAdmin(admin.ModelAdmin):
    list_display = ("__str__", "slug", "description", "model", "active", "sort_order")
    list_editable = ("active", "sort_order")
    list_filter = ("active",)
    prepopulated_fields = {"slug": ("name",)}


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    can_delete = False
    fields = ("created_at", "role", "text", "photos", "voice", "job")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """For support: what a user asked and was answered. Users delete their own."""

    list_display = ("__str__", "account", "model", "assistant", "updated_at")
    list_filter = ("model", "assistant")
    search_fields = ("=id", "account__telegram_user_id", "account__username", "title")
    list_select_related = ("account", "model", "assistant")
    show_full_result_count = False
    raw_id_fields = ("account",)
    readonly_fields = ("account", "title", "model", "assistant", "created_at", "updated_at")
    inlines = (MessageInline,)

    def has_add_permission(self, request):
        return False


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    """Every paid request: what it held, what it cost, what it was charged."""

    list_display = ("__str__", "account", "model", "hold", "charged", "cost_usd", "demo",
                    "created_at")
    list_filter = ("kind", "status", "demo", "model", "created_at")
    search_fields = ("=id", "account__telegram_user_id", "account__username", "prompt")
    list_select_related = ("account", "model")
    show_full_result_count = False
    readonly_fields = [field.name for field in Job._meta.fields]
    actions = ("give_back",)

    @admin.display(description="cost (USD)")
    def cost_usd(self, obj):
        return f"{obj.cost_micros / 1e6:.4f}" if obj.cost_micros else "—"

    @admin.action(description="Fail and return the credits (requests still being made)")
    def give_back(self, request, queryset):
        count = 0
        for job in queryset.filter(status__in=ACTIVE).select_related("account", "model"):
            if jobs.failed(job, f"stopped in the admin by {request.user}",
                           by=request.user.get_username()):
                count += 1
        self.message_user(request, f"{count} request(s) stopped, their credits returned.",
                          messages.SUCCESS if count else messages.WARNING)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
