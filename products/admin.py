from django.contrib import admin
from django.utils.html import format_html
from .models import Product, ProductCategory, ProductVariant, VariantAttribute


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


class VariantAttributeInline(admin.TabularInline):
    model = VariantAttribute
    extra = 1


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ('sku', 'price_adjustment', 'stock_quantity', 'low_stock_threshold', 'is_active')
    show_change_link = True


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = (
        'display_name',
        'product',
        'sku',
        'final_price',
        'stock_badge',
        'is_active',
    )
    list_filter = ('is_active', 'product__business')
    search_fields = ('sku', 'product__name')
    inlines = [VariantAttributeInline]

    @admin.display(description='Stock')
    def stock_badge(self, obj):
        if obj.stock_quantity == 0:
            colour, label = '#ef4444', 'OUT OF STOCK'
        elif obj.is_low_stock:
            colour, label = '#f59e0b', f'{obj.stock_quantity} (LOW)'
        else:
            colour, label = '#10b981', str(obj.stock_quantity)

        return format_html(
            '<span style="background:{};color:white;padding:2px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            colour,
            label,
        )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'business',
        'category',
        'sku',
        'price',
        'variant_count',
        'stock_badge',
        'is_active',
        'created_at',
    )
    list_filter = ('is_active', 'category')
    search_fields = ('name', 'sku', 'business__name')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductVariantInline]

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

    @admin.display(description='Variants')
    def variant_count(self, obj):
        count = obj.variants.count()
        return count if count else '—'

    @admin.display(description='Stock')
    def stock_badge(self, obj):
        # Uses total_stock so it reflects variant stock when present
        total = obj.total_stock

        if total == 0:
            colour, label = '#ef4444', 'OUT OF STOCK'
        elif not obj.has_variants and obj.is_low_stock:
            colour, label = '#f59e0b', f'{total} (LOW)'
        else:
            colour, label = '#10b981', str(total)

        return format_html(
            '<span style="background:{};color:white;padding:2px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            colour,
            label,
        )