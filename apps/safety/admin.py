from django.contrib import admin, messages

from . import strikes
from .models import BlockedTerm, Check, Policy, Result, Review, ReviewItem, Screening


@admin.register(BlockedTerm)
class BlockedTermAdmin(admin.ModelAdmin):
    list_display = ("term", "category", "active", "chat", "note", "created_at")
    list_editable = ("category", "active", "chat")
    list_filter = ("active", "chat", "category")
    search_fields = ("term", "note")


@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ("__str__", "updated_at")
    fields = ("text", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not Policy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # One policy: the list is just a way in.
        from django.shortcuts import redirect

        return redirect("admin:safety_policy_change", Policy.get().pk)


class CheckInline(admin.TabularInline):
    model = Check
    extra = 0
    can_delete = False
    fields = ("layer", "verdict", "category", "detail", "cost", "duration_ms")
    readonly_fields = fields

    @admin.display(description="cost")
    def cost(self, obj):
        return f"${obj.cost_micros / 1e6:.4f}" if obj.cost_micros else "—"

    def has_add_permission(self, request, obj=None):
        return False


class ScreeningAdminBase(admin.ModelAdmin):
    list_display = ("__str__", "account", "tool", "model", "layer", "category", "strike",
                    "review", "created_at")
    list_filter = ("result", "review", "tool", "layer", "category")
    search_fields = ("=id", "account__telegram_user_id", "account__username", "prompt")
    list_select_related = ("account",)
    show_full_result_count = False
    inlines = (CheckInline,)
    actions = ("confirm_block", "overturn_block")
    readonly_fields = [field.name for field in Screening._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Confirm the block")
    def confirm_block(self, request, queryset):
        count = 0
        for screening in queryset.filter(result=Result.BLOCKED):
            strikes.confirm(screening, request.user.get_username())
            count += 1
        self.message_user(request, f"{count} block(s) confirmed.")

    @admin.action(description="Not a violation — take the strike back")
    def overturn_block(self, request, queryset):
        for screening in queryset.filter(result=Result.BLOCKED).exclude(
                review=Review.OVERTURNED):
            account = strikes.overturn(screening, request.user.get_username())
            note = f"#S{screening.pk}: strike taken back, {account} has {account.strikes}."
            if account.is_blocked:
                self.message_user(request, note + " The account is still blocked: unblock it "
                                         "in Accounts if this block was the reason.",
                                  messages.WARNING)
            else:
                self.message_user(request, note)


@admin.register(Screening)
class ScreeningAdmin(ScreeningAdminBase):
    pass


@admin.register(ReviewItem)
class ReviewItemAdmin(ScreeningAdminBase):
    """Only the blocked requests; filter by review for the ones still waiting."""

    list_filter = ("review", "layer", "category")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(result=Result.BLOCKED)
