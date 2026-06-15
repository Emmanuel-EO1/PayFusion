from django.contrib import admin
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
    list_display = ('name', 'business', 'category', 'price', 'is_active', 'created_at')
    list_filter = ('is_active', 'category')
    search_fields = ('name', 'business__name')
    prepopulated_fields = {'slug': ('name',)}