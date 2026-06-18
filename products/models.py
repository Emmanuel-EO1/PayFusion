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

    sku = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text='Optional stock keeping unit code, e.g. NIKE-AF1-WHT-42.'
    )

    # Stock for SIMPLE products (no variants).
    # When has_variants is True, this field is ignored —
    # each ProductVariant tracks its own stock instead.
    stock_quantity = models.PositiveIntegerField(
        default=0,
        help_text='Units currently available. Ignored if this product has variants.'
    )

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
    def has_variants(self):
        """True if this product has at least one variant defined."""
        return self.variants.exists()

    @property
    def is_low_stock(self):
        """
        True when stock has fallen to or below the threshold.
        For products with variants, this checks the product-level
        fields directly — use variant.is_low_stock for per-variant
        status instead.
        """
        return self.stock_quantity <= self.low_stock_threshold

    @property
    def is_in_stock(self):
        """
        True when at least one unit is available.
        For products with variants, True if ANY variant is in stock.
        """
        if self.has_variants:
            return self.variants.filter(stock_quantity__gt=0).exists()
        return self.stock_quantity > 0

    @property
    def total_stock(self):
        """
        Combined stock across all variants, or the product's own
        stock_quantity if it has no variants. Useful for displaying
        a single stock figure regardless of whether variants exist.
        """
        if self.has_variants:
            return sum(v.stock_quantity for v in self.variants.all())
        return self.stock_quantity

    @property
    def display_price_range(self):
        """
        Returns the price range across variants if variants exist
        and have differing adjusted prices, otherwise the base price.
        Used on product listing pages to show e.g. "₦5,000 - ₦5,500".
        """
        if not self.has_variants:
            return None

        prices = [v.final_price for v in self.variants.all()]
        if not prices:
            return None

        low, high = min(prices), max(prices)
        if low == high:
            return f'{low:.2f}'
        return f'{low:.2f} - {high:.2f}'


# ============================================================
# PRODUCT VARIANT MODEL
#
# Represents one purchasable option under a parent Product —
# e.g. a specific size/colour combination. Each variant has
# its own stock and an optional price adjustment relative to
# the parent product's base price.
#
# Attributes (size, colour, material, etc.) are NOT fixed
# columns on this model — they are stored flexibly via the
# related VariantAttribute model below. This means a vendor
# selling clothing can use Size/Colour, while a vendor selling
# electronics can use Storage/Colour, without any schema change.
#
# A product with zero ProductVariant rows is a "simple" product
# and uses Product.stock_quantity directly (Phase 10 Step 1
# behaviour, unchanged).
# ============================================================
class ProductVariant(models.Model):

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='variants'
    )

    # Optional unique code for this specific variant.
    # Same null=True pattern as Product.sku — many variants
    # may have no SKU, and PostgreSQL exempts NULL from the
    # uniqueness check so multiple blank SKUs don't collide.
    sku = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text='Optional variant-specific SKU, e.g. NIKE-AF1-WHT-42.'
    )

    # Added to or subtracted from the parent product's base price.
    # Example: base price ₦5,000, black colourway price_adjustment
    # of ₦500 means this variant sells for ₦5,500.
    # Can be negative for a cheaper variant (e.g. a smaller size
    # priced lower than the base).
    price_adjustment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Added to the base product price. Can be negative.'
    )

    stock_quantity = models.PositiveIntegerField(
        default=0,
        help_text='Units of this specific variant currently available.'
    )

    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text='Flag as low stock when this variant falls to or below this.'
    )

    is_active = models.BooleanField(
        default=True,
        help_text='Inactive variants are hidden from customers but preserved for order history.'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        indexes = [
            models.Index(fields=['product', 'is_active'], name='variant_product_active_idx'),
        ]

    def __str__(self):
        attrs = ', '.join(
            f'{a.name}: {a.value}' for a in self.attributes.all()
        )
        return f'{self.product.name} ({attrs})' if attrs else f'{self.product.name} (variant)'

    @property
    def final_price(self):
        """The actual sellable price for this variant."""
        return self.product.price + self.price_adjustment

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.low_stock_threshold

    @property
    def is_in_stock(self):
        return self.stock_quantity > 0

    @property
    def display_name(self):
        """
        Human-readable label combining product name and attributes.
        e.g. "Nike Air Force 1 — Size: 42, Colour: Black"
        Used in cart, checkout, and order history displays.
        """
        attrs = ', '.join(
            f'{a.name}: {a.value}' for a in self.attributes.all()
        )
        if attrs:
            return f'{self.product.name} — {attrs}'
        return self.product.name


# ============================================================
# VARIANT ATTRIBUTE MODEL
#
# Flexible key-value attribute for a ProductVariant.
# A variant can have multiple attributes (e.g. Size AND Colour).
# This design lets any vendor define whatever attribute types
# fit their product category without requiring schema changes.
#
# Example rows for one variant:
#   (variant=X, name="Size",   value="42")
#   (variant=X, name="Colour", value="Black")
# ============================================================
class VariantAttribute(models.Model):

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name='attributes'
    )

    name = models.CharField(
        max_length=50,
        help_text='Attribute type, e.g. Size, Colour, Material.'
    )
    value = models.CharField(
        max_length=100,
        help_text='Attribute value, e.g. 42, Black, Leather.'
    )

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['variant'], name='variant_attr_idx'),
        ]
        # Prevents the same attribute name being set twice
        # on the same variant (e.g. two "Size" rows for one variant).
        constraints = [
            models.UniqueConstraint(
                fields=['variant', 'name'],
                name='unique_attribute_per_variant'
            )
        ]

    def __str__(self):
        return f'{self.name}: {self.value}'