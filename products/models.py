from decimal import Decimal

from django.db import models
from django.utils.text import slugify
from tenants.models import Business


class ProductCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            'Optional category commission rate. '
            'Overrides platform default for all products in this category. '
            '0.08 = 8%. Leave blank to use platform default.'
        )
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Product Category'
        verbose_name_plural = 'Product Categories'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Product(models.Model):

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name='products'
    )

    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    # --- Inventory (Phase 10 Step 1) ---

    # Vendor-facing stock code. Optional and unique when set —
    # vendors who already track SKUs elsewhere can adopt this
    # gradually. Blank means no SKU assigned.
    sku = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text='Optional stock keeping unit code, e.g. NIKE-AF1-WHT-42.'
    )

    # Current available units for this product.
    # For products with variants (Phase 10 Step 2), this field
    # becomes unused — stock is tracked per-variant instead.
    # Decremented atomically on payment confirmation
    # (transactions/views.py charge.success handler).
    # Restored on dispute resolution with refund_issued
    # (disputes/signals.py).
    stock_quantity = models.PositiveIntegerField(
        default=0,
        help_text='Units currently available for sale.'
    )

    # When stock_quantity falls to or below this number,
    # the product is flagged as low stock in the vendor dashboard.
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text='Flag as low stock when quantity falls to or below this.'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', 'is_active'], name='product_biz_active_idx'),
            models.Index(fields=['category'], name='product_category_idx'),
        ]

    def __str__(self):
        return f'{self.name} - {self.business.name}'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def is_low_stock(self):
        """True when stock has fallen to or below the threshold."""
        return self.stock_quantity <= self.low_stock_threshold

    @property
    def is_in_stock(self):
        """True when at least one unit is available."""
        return self.stock_quantity > 0