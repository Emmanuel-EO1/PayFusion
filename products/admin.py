from django.contrib import admin
from django.utils.html import format_html
from .models import Product, ProductCategory, ProductVariant, VariantAttribute, Tag, Review, ReviewResponse


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'product_count', 'created_at')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}

    @admin.display(description='Products')
    def product_count(self, obj):
        return obj.products.count()


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
        'tags',
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

@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('product', 'user', 'rating', 'is_hidden', 'created_at')
    list_filter = ('rating', 'is_hidden', 'created_at')
    search_fields = ('product__name', 'user__email', 'body')
    readonly_fields = ('product', 'user', 'order', 'rating', 'body', 'created_at', 'updated_at')
    actions = ['hide_reviews', 'unhide_reviews']

    @admin.action(description='Hide selected reviews')
    def hide_reviews(self, request, queryset):
        queryset.update(is_hidden=True)

    @admin.action(description='Unhide selected reviews')
    def unhide_reviews(self, request, queryset):
        queryset.update(is_hidden=False)


@admin.register(ReviewResponse)
class ReviewResponseAdmin(admin.ModelAdmin):
    list_display = ('review', 'vendor_business', 'created_at')
    search_fields = ('review__product__name', 'vendor_business__name', 'body')
    readonly_fields = ('review', 'vendor_business', 'created_at')