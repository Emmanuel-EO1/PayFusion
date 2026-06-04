from django.contrib import admin
from django.utils.html import format_html
from .models import Business, Wallet, BankAccount, WithdrawalRequest


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
            '<span style="color:{};font-weight:600;">&#8358;{}</span>',
            colour,
            f'{obj.balance:,.2f}',
        )


# ============================================================
# BANK ACCOUNT ADMIN
# ============================================================
@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):

    list_display = (
        'business',
        'account_name',
        'masked_account_number',
        'bank_name',
        'is_verified_badge',
        'is_primary',
        'is_active',
        'created_at',
    )

    list_filter = (
        'is_verified',
        'is_primary',
        'is_active',
        'bank_name',
    )

    search_fields = (
        'business__name',
        'account_name',
        'account_number',
        'bank_name',
        'recipient_code',
    )

    readonly_fields = (
        'business',
        'account_name',
        'account_number',
        'bank_name',
        'bank_code',
        'recipient_code',
        'is_verified',
        'created_at',
        'updated_at',
    )

    fields = (
        'business',
        'account_name',
        'account_number',
        'bank_name',
        'bank_code',
        'recipient_code',
        'is_verified',
        'is_primary',
        'is_active',
        'created_at',
        'updated_at',
    )

    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Account Number')
    def masked_account_number(self, obj):
        return f'****{obj.account_number[-4:]}'

    @admin.display(description='Verified')
    def is_verified_badge(self, obj):
        if obj.is_verified:
            return format_html(
                '<span style="'
                'background:#10b981;'
                'color:white;'
                'padding:2px 10px;'
                'border-radius:12px;'
                'font-size:11px;'
                'font-weight:600;'
                '">VERIFIED</span>'
            )
        return format_html(
            '<span style="'
            'background:#f59e0b;'
            'color:white;'
            'padding:2px 10px;'
            'border-radius:12px;'
            'font-size:11px;'
            'font-weight:600;'
            '">PENDING</span>'
        )


# ============================================================
# WITHDRAWAL REQUEST ADMIN
# Full visibility into every withdrawal request on the platform.
# Admins can approve, reject, and review flagged requests.
# Financial fields are read-only — only status decisions
# and review notes can be entered by admins.
# ============================================================
@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):

    list_display = (
        'business',
        'amount_display',
        'status_badge',
        'bank_account',
        'requires_admin_review',
        'hold_expires_at',
        'created_at',
        'completed_at',
    )

    list_filter = (
        'status',
        'requires_admin_review',
        'audit_passed',
        'created_at',
    )

    search_fields = (
        'business__name',
        'business__owner__email',
        'transaction__reference',
        'transfer_code',
        'rejection_reason',
    )

    readonly_fields = (
        'business',
        'bank_account',
        'transaction',
        'amount',
        'hold_expires_at',
        'audit_passed',
        'audit_notes',
        'transfer_code',
        'created_at',
        'updated_at',
        'processing_started_at',
        'completed_at',
    )

    fields = (
        'business',
        'amount',
        'bank_account',
        'transaction',
        'status',
        'hold_expires_at',
        'audit_passed',
        'audit_notes',
        'requires_admin_review',
        'reviewed_by',
        'review_notes',
        'transfer_code',
        'rejection_reason',
        'created_at',
        'updated_at',
        'processing_started_at',
        'completed_at',
    )

    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Amount')
    def amount_display(self, obj):
        return f'&#8358;{obj.amount:,.2f}'

    @admin.display(description='Status')
    def status_badge(self, obj):
        colours = {
            'pending_audit':    '#3b82f6',
            'audit_failed':     '#ef4444',
            'pending_hold':     '#f59e0b',
            'pending_approval': '#8b5cf6',
            'approved':         '#10b981',
            'processing':       '#3b82f6',
            'completed':        '#10b981',
            'failed':           '#ef4444',
            'rejected':         '#6b7280',
        }
        colour = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="'
            'background:{};'
            'color:white;'
            'padding:2px 10px;'
            'border-radius:12px;'
            'font-size:11px;'
            'font-weight:600;'
            '">{}</span>',
            colour,
            obj.get_status_display().upper(),
        )