from django.contrib import admin
from django.utils.html import format_html
from .models import PlatformConfig


# ============================================================
# PLATFORM CONFIG ADMIN
#
# Single-record configuration panel.
# Admins edit the one existing record — cannot
# create new ones or delete the existing one.
# ============================================================
@admin.register(PlatformConfig)
class PlatformConfigAdmin(admin.ModelAdmin):

    list_display = (
        'config_summary',
        'commission_rate_display',
        'withdrawal_hold_days',
        'minimum_withdrawal_amount_display',
        'auto_approval_threshold_display',
        'updated_at',
        'updated_by',
    )

    readonly_fields = (
        'updated_at',
        'updated_by',
    )

    fieldsets = (
        ('Commission Settings', {
            'fields': ('commission_rate',),
            'description': (
                'Controls how much the platform takes from each vendor sale. '
                'Changes take effect immediately on the next transaction.'
            ),
        }),
        ('Withdrawal Policy', {
            'fields': (
                'withdrawal_hold_days',
                'minimum_withdrawal_amount',
                'auto_approval_threshold',
            ),
            'description': (
                'Controls vendor withdrawal behaviour. '
                'Hold days apply to all new withdrawal requests from the moment of change.'
            ),
        }),
        ('Fraud Detection', {
            'fields': (
                'fraud_failed_tx_threshold',
                'fraud_lookback_hours',
            ),
            'description': (
                'Controls automatic fraud flagging during withdrawal audits. '
                'Flagged withdrawals require manual admin review before transfer.'
            ),
        }),
        ('Audit Trail', {
            'fields': (
                'updated_at',
                'updated_by',
            ),
            'classes': ('collapse',),
        }),
    )

    def has_add_permission(self, request):
        return not PlatformConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='Configuration')
    def config_summary(self, obj):
        return format_html('<strong>PayFusion Platform Config</strong>')

    @admin.display(description='Commission Rate')
    def commission_rate_display(self, obj):
        rate_percent = obj.commission_rate * 100
        return format_html(
            '<span style="color:#10b981;font-weight:600;">{}%</span>',
            f'{rate_percent:.1f}',
        )

    @admin.display(description='Min Withdrawal')
    def minimum_withdrawal_amount_display(self, obj):
        return f'N{obj.minimum_withdrawal_amount:,.2f}'

    @admin.display(description='Auto-Approval Limit')
    def auto_approval_threshold_display(self, obj):
        return f'N{obj.auto_approval_threshold:,.2f}'