from django.contrib import admin
from django.utils.html import format_html
from .models import Delivery, DeliveryStatusHistory


class DeliveryStatusHistoryInline(admin.TabularInline):
    model = DeliveryStatusHistory
    extra = 0
    readonly_fields = ('status', 'changed_by', 'note', 'timestamp')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):

    list_display = (
        'order',
        'vendor',
        'customer',
        'status_badge',
        'tracking_id',
        'courier',
        'estimated_delivery_date',
        'created_at',
    )

    list_filter = ('status', 'created_at')

    search_fields = (
        'order__reference',
        'order__business__name',
        'order__user__email',
        'tracking_id',
        'courier',
    )

    readonly_fields = (
        'order',
        'accepted_at',
        'shipped_at',
        'delivered_at',
        'cancelled_at',
        'created_at',
        'updated_at',
    )

    inlines = [DeliveryStatusHistoryInline]

    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Vendor')
    def vendor(self, obj):
        return obj.order.business.name

    @admin.display(description='Customer')
    def customer(self, obj):
        return obj.order.user.email

    @admin.display(description='Status')
    def status_badge(self, obj):
        colours = {
            'pending':    '#f59e0b',
            'processing': '#3b82f6',
            'shipped':    '#8b5cf6',
            'in_transit': '#06b6d4',
            'delivered':  '#10b981',
            'cancelled':  '#ef4444',
            'returned':   '#6b7280',
        }
        colour = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            colour,
            obj.get_status_display().upper(),
        )


@admin.register(DeliveryStatusHistory)
class DeliveryStatusHistoryAdmin(admin.ModelAdmin):

    list_display = (
        'delivery',
        'status',
        'changed_by',
        'note',
        'timestamp',
    )

    list_filter = ('status', 'timestamp')

    search_fields = (
        'delivery__order__reference',
        'note',
    )

    readonly_fields = (
        'delivery',
        'status',
        'changed_by',
        'note',
        'timestamp',
    )

    ordering = ('-timestamp',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False