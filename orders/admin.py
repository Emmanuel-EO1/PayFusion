from django.contrib import admin
from .models import Order, OrderItem

# Register your models here.
class OrderItemInLine(admin.TabularInline):
    model = OrderItem
    extra = 0

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['reference', 'business', 'status', 'get_customer_email', 'total_amount', 'created_at']

    list_filter = ['business', 'status', 'created_at']

    readonly_fields = ['reference', 'total_amount', 'created_at', 'updated_at']
    inlines = [OrderItemInLine]

    @admin.display(description='Customer Email', ordering='user__email')
    def get_customer_email(self, obj):
        return obj.user.email

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'product', 'quantity', 'price']
    # readonly_fields = ['price']