from django.contrib import admin
from django.utils.html import format_html
from .models import Product, ProductCategory


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'commission_rate_display', 'created_at')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}

    @admin.display(description='Commission Rate')
    def commission_rate_display(self, obj):
        if obj.commission_rate is None:
            return 'Platform default'
        return f'{obj.commission_rate * 100:.2f}%'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'business',
        'category',
        'sku',
        'price',
        'stock_badge',
        'is_active',
        'created_at',
    )
    list_filter = ('is_active', 'category')
    search_fields = ('name', 'sku', 'business__name')
    prepopulated_fields = {'slug': ('name',)}

    fields = (
        'business',
        'category',
        'name',
        'slug',
        'description',
        'price',
        'sku',
        'stock_quantity',
        'low_stock_threshold',
        'is_active',
    )

    @admin.display(description='Stock')
    def stock_badge(self, obj):
        if obj.stock_quantity == 0:
            colour = '#ef4444'
            label = 'OUT OF STOCK'
        elif obj.is_low_stock:
            colour = '#f59e0b'
            label = f'{obj.stock_quantity} (LOW)'
        else:
            colour = '#10b981'
            label = str(obj.stock_quantity)

        return format_html(
            '<span style="background:{};color:white;padding:2px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            colour,
            label,
        )