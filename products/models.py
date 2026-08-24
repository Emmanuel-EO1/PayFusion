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


# ============================================================
# TAG MODEL (Phase 10 Step 3)
#
# A shared vocabulary of short labels vendors can attach to
# their products. A separate model (rather than free text on
# Product) so every vendor tags into the same consistent set
# of names — "shoe-accessory" always means the same thing,
# rather than three different vendors typing three different
# variations of the same idea.
#
# Immediate use: vendor-side organisation and filtering in
# their own product management view.
# Future use: groundwork for Phase 15's combination engine,
# where tags become one signal (alongside AI embeddings) for
# matching products customers combine together.
# ============================================================
class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=50, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Normalise before saving so near-identical tags collapse
        # into one — "Shoe-Accessory", "shoe accessory", and
        # "shoe-accessory " all become "shoe accessory".
        # Hyphens are treated as equivalent to spaces, since in
        # short tag labels they almost always mean the same thing
        # (e.g. "shoe-accessory" == "shoe accessory").
        # NOTE: this only catches exact/near-exact duplicates.
        # Genuinely different-but-related tags (e.g. "boot accessory"
        # vs "shoe accessory") are NOT unified by this — that
        # semantic relationship is intentionally deferred to
        # Phase 15's AI embedding layer, which can recognise
        # conceptual closeness that string normalisation cannot.
        self.name = ' '.join(
            self.name.strip().lower().replace('-', ' ').split()
        )
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_normalised(cls, raw_name):
        """
        Looks up or creates a Tag using the same normalisation
        rule as save(), so callers never accidentally create a
        near-duplicate by checking against the raw, un-normalised
        input. Use this instead of Tag.objects.get_or_create()
        directly when creating tags from vendor-typed input.
        """
        normalised = ' '.join(
            raw_name.strip().lower().replace('-', ' ').split()
        )
        if not normalised:
            return None
        tag, _ = cls.objects.get_or_create(name=normalised)
        return tag


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

    # Many-to-many: a product can have multiple tags, and a tag
    # can apply to many products. blank=True since tagging is
    # entirely optional — most products may never be tagged.
    tags = models.ManyToManyField(
        Tag,
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
        return self.variants.exists()

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.low_stock_threshold

    @property
    def is_in_stock(self):
        if self.has_variants:
            return self.variants.filter(stock_quantity__gt=0).exists()
        return self.stock_quantity > 0

    @property
    def total_stock(self):
        if self.has_variants:
            return sum(v.stock_quantity for v in self.variants.all())
        return self.stock_quantity

    @property
    def display_price_range(self):
        if not self.has_variants:
            return None

        prices = [v.final_price for v in self.variants.all()]
        if not prices:
            return None

        low, high = min(prices), max(prices)
        if low == high:
            return f'{low:.2f}'
        return f'{low:.2f} - {high:.2f}'


class ProductVariant(models.Model):

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='variants'
    )

    sku = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text='Optional variant-specific SKU, e.g. NIKE-AF1-WHT-42.'
    )

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
        return self.product.price + self.price_adjustment

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.low_stock_threshold

    @property
    def is_in_stock(self):
        return self.stock_quantity > 0

    @property
    def display_name(self):
        attrs = ', '.join(
            f'{a.name}: {a.value}' for a in self.attributes.all()
        )
        if attrs:
            return f'{self.product.name} — {attrs}'
        return self.product.name


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
        constraints = [
            models.UniqueConstraint(
                fields=['variant', 'name'],
                name='unique_attribute_per_variant'
            )
        ]

    def __str__(self):
        return f'{self.name}: {self.value}'


class Review(models.Model):

    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='reviews'
    )
    user = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='reviews'
    )
    # The delivered order that proves the purchase
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='reviews'
    )

    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    body = models.TextField()
    is_hidden = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'user'],
                name='unique_review_per_user_per_product'
            )
        ]
        indexes = [
            models.Index(fields=['product', 'is_hidden'], name='review_product_idx'),
            models.Index(fields=['user'], name='review_user_idx'),
        ]

    def __str__(self):
        return f'{self.user.email} — {self.product.name} ({self.rating}★)'


class ReviewResponse(models.Model):

    review = models.OneToOneField(
        Review,
        on_delete=models.CASCADE,
        related_name='response'
    )
    vendor_business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name='review_responses'
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Response to review #{self.review.id}'