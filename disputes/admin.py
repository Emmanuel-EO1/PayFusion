from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from .models import Dispute


# ============================================================
# DISPUTE ADMIN
# Admins can investigate and resolve disputes.
# Customer-submitted fields are read-only.
# Admins fill in resolution, resolution_notes, resolved_by.
# ============================================================
@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'order',
        'business',
        'raised_by',
        'reason_display',
        'amount_disputed_display',
        'status_badge',
        'created_at',
        'resolved_at',
    )

    list_filter = (
        'status',
        'reason',
        'resolution',
        'created_at',
    )

    search_fields = (
        'order__id',
        'business__name',
        'raised_by__email',
        'description',
        'resolution_notes',
    )

    # Customer-submitted fields — read only
    readonly_fields = (
        'order',
        'business',
        'raised_by',
        'reason',
        'description',
        'amount_disputed',
        'evidence_note',
        'created_at',
        'updated_at',
        'resolved_at',
    )

    # Field layout in detail view
    fields = (
        'order',
        'business',
        'raised_by',
        'reason',
        'description',
        'amount_disputed',
        'evidence_note',
        'status',
        'resolution',
        'resolution_notes',
        'resolved_by',
        'created_at',
        'updated_at',
        'resolved_at',
    )

    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        # Disputes are only raised by customers via the platform
        return False

    def has_delete_permission(self, request, obj=None):
        # Disputes are permanent records
        return False

    def save_model(self, request, obj, form, change):
        # Auto-set resolved_at and resolved_by when
        # admin marks a dispute as resolved
        if change and obj.status == 'resolved' and not obj.resolved_at:
            obj.resolved_at = timezone.now()
            obj.resolved_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='Reason')
    def reason_display(self, obj):
        return obj.get_reason_display()

    @admin.display(description='Amount Disputed')
    def amount_disputed_display(self, obj):
        return f'₦{obj.amount_disputed:,.2f}'

    @admin.display(description='Status')
    def status_badge(self, obj):
        colours = {
            'open':         '#ef4444',  # red — needs attention
            'under_review': '#f59e0b',  # amber — in progress
            'resolved':     '#10b981',  # green — done
            'closed':       '#6b7280',  # grey — archived
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