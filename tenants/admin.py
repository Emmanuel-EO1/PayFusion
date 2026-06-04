from django.contrib import admin
from django.utils.html import format_html
from .models import Business, Wallet


# ============================================================
# BUSINESS ADMIN
# ============================================================
@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):

    list_display = (
        'name',
        'owner',
        'industry',
        'state',
        'country',
        'created_at',
    )

    list_filter = (
        'industry',
        'country',
        'state',
    )

    search_fields = (
        'name',
        'industry',
        'state',
        'country',
        'owner__email',
    )

    prepopulated_fields = {'slug': ('name',)}

    readonly_fields = (
        'created_at',
        'updated_at',
    )

    ordering = ('-created_at',)
    date_hierarchy = 'created_at'


# ============================================================
# WALLET ADMIN
# View-only access to all vendor wallet balances.
# Admins can monitor balances but cannot edit them directly.
# All balance changes must go through the transaction system.
# ============================================================
@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):

    list_display = (
        'business',
        'balance_display',
        'currency',
        'updated_at',
    )

    search_fields = (
        'business__name',
        'business__owner__email',
    )

    readonly_fields = (
        'business',
        'balance',
        'currency',
        'created_at',
        'updated_at',
    )

    ordering = ('-balance',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Balance')
    def balance_display(self, obj):
        colour = '#10b981' if obj.balance > 0 else '#6b7280'
        return format_html(
            '<span style="color:{};font-weight:600;">₦{}</span>',
            colour,
            f'{obj.balance:,.2f}',
        )