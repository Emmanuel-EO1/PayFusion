from django.contrib import admin
from django.utils.html import format_html
from .models import Transaction, WebhookEvent, EscrowEntry


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):

    list_display = (
        'reference',
        'user',
        'business',
        'amount_display',
        'transaction_type',
        'status_badge',
        'created_at',
    )

    list_filter = (
        'status',
        'transaction_type',
        'created_at',
    )

    search_fields = (
        'reference',
        'business__name',
        'user__email',
        'description',
    )

    readonly_fields = (
        'reference',
        'user',
        'business',
        'amount',
        'transaction_type',
        'status',
        'description',
        'created_at',
        'updated_at',
    )

    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Amount')
    def amount_display(self, obj):
        return f'₦{obj.amount:,.2f}'

    @admin.display(description='Status')
    def status_badge(self, obj):
        colours = {
            'pending':   '#f59e0b',
            'paid':      '#10b981',
            'completed': '#10b981',
            'failed':    '#ef4444',
        }
        colour = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            colour,
            obj.status.upper(),
        )


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):

    list_display = (
        'event_type',
        'reference',
        'status_badge',
        'received_at',
        'processed_at',
        'event_id',
    )

    list_filter = (
        'status',
        'event_type',
        'received_at',
    )

    search_fields = (
        'reference',
        'event_id',
        'event_type',
        'error_message',
    )

    readonly_fields = (
        'event_id',
        'event_type',
        'reference',
        'payload_display',
        'status',
        'error_message',
        'received_at',
        'processed_at',
    )

    exclude = ('payload',)
    ordering = ('-received_at',)
    date_hierarchy = 'received_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Status')
    def status_badge(self, obj):
        colours = {
            'received':  '#3b82f6',
            'processed': '#10b981',
            'failed':    '#ef4444',
            'duplicate': '#f59e0b',
            'ignored':   '#6b7280',
        }
        colour = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            colour,
            obj.status.upper(),
        )

    @admin.display(description='Payload')
    def payload_display(self, obj):
        import json
        formatted = json.dumps(obj.payload, indent=2)
        return format_html(
            '<pre style="background:#1e1e1e;color:#d4d4d4;padding:16px;'
            'border-radius:8px;font-size:12px;overflow-x:auto;'
            'max-height:400px;">{}</pre>',
            formatted,
        )


# ============================================================
# ESCROW ENTRY ADMIN
# Full audit trail of every escrow hold and release.
# Admins can see is_frozen status and manually inspect
# the lifecycle of any order's escrow funds.
# All fields read-only — escrow movements are system-generated.
# ============================================================
@admin.register(EscrowEntry)
class EscrowEntryAdmin(admin.ModelAdmin):

    list_display = (
        'order',
        'business',
        'entry_type_badge',
        'amount_display',
        'frozen_badge',
        'created_at',
    )

    list_filter = (
        'entry_type',
        'is_frozen',
        'created_at',
    )

    search_fields = (
        'order__reference',
        'business__name',
        'transaction__reference',
        'description',
    )

    readonly_fields = (
        'business',
        'order',
        'transaction',
        'entry_type',
        'amount',
        'is_frozen',
        'description',
        'created_at',
    )

    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Amount')
    def amount_display(self, obj):
        return f'₦{obj.amount:,.2f}'

    @admin.display(description='Type')
    def entry_type_badge(self, obj):
        colour = '#f59e0b' if obj.entry_type == 'hold' else '#10b981'
        return format_html(
            '<span style="background:{};color:white;padding:2px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            colour,
            obj.entry_type.upper(),
        )

    @admin.display(description='Frozen')
    def frozen_badge(self, obj):
        if obj.is_frozen:
            return format_html(
                '<span style="background:#ef4444;color:white;padding:2px 10px;'
                'border-radius:12px;font-size:11px;font-weight:600;">FROZEN</span>'
            )
        return format_html('<span style="color:#6b7280;">—</span>')