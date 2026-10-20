from django.contrib import admin, messages
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce

from . import jobs
from .models import ACTIVE, Assistant, Category, Conversation, Job, Message, Status


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """The shelves of the agent catalog. Names are translated like the bot's texts."""

    list_display = ("__str__", "slug", "agents_on_offer", "active", "sort_order")
    list_editable = ("active", "sort_order")
    prepopulated_fields = {"slug": ("name",)}
    actions = ("draft_agents",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            on_offer=Count("agents", filter=Q(agents__active=True)))

    @admin.display(description="agents on offer", ordering="on_offer")
    def agents_on_offer(self, obj):
        return obj.on_offer

    @admin.action(description="Draft 3 new agents with AI (switched off, for you to review)")
    def draft_agents(self, request, queryset):
        from . import tasks

        for category in queryset:
            tasks.draft_agents.delay(category.pk)
        self.message_user(
            request, f"Drafting agents for {queryset.count()} categor(y/ies). They appear under "
            "Agents, switched off, in a minute; check and switch on the good ones. A failure "
            "is in the activity log.", messages.SUCCESS)


@admin.register(Assistant)
class AssistantAdmin(admin.ModelAdmin):
    """
    An agent: its instructions (in English: the AI answers in the user's
    language), what users read on its card, and how much it is used.
    """

    list_display = ("__str__", "slug", "category", "model", "featured", "chats", "credits",
                    "active", "sort_order")
    list_editable = ("featured", "active", "sort_order")
    list_filter = ("active", "featured", "category")
    search_fields = ("slug", "name", "description", "tags")
    list_select_related = ("category", "model")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("name", "slug", "emoji", "category", "active", "featured",
                           "sort_order")}),
        ("What users read", {"fields": ("description", "first_message", "starters", "tags")}),
        ("What the AI is told", {"fields": ("instructions", "model")}),
    )

    def get_queryset(self, request):
        charged = Job.objects.filter(assistant=OuterRef("pk"), status=Status.DONE).order_by() \
            .values("assistant").annotate(total=Sum("charged")).values("total")
        return super().get_queryset(request).annotate(
            chat_count=Count("conversations", distinct=True),
            credit_total=Coalesce(Subquery(charged, output_field=IntegerField()), 0))

    @admin.display(description="chats", ordering="chat_count")
    def chats(self, obj):
        return obj.chat_count

    @admin.display(description="credits charged", ordering="credit_total")
    def credits(self, obj):
        return obj.credit_total


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
