from decimal import Decimal

from django.db import models
from django.conf import settings
from products.models import Product, ProductVariant
from tenants.models import Business
from transactions.models import Transaction
import uuid


class Order(models.Model):

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='orders')
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='orders')

    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    commission_rate_applied = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        help_text='The commission rate actually applied to this order at settlement time.'
    )
    commission_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='The exact commission amount (Naira) taken from this order.'
    )

    reference = models.CharField(max_length=50, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['status', '-created_at'], name='order_status_date_idx'),
        ]

    def __str__(self):
        return f'{self.reference} - {self.user}'

    def calculate_total(self):
        total = sum(item.price * item.quantity for item in self.items.all())
        self.total_amount = total
        self.save(update_fields=['total_amount'])

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f'ORD-{uuid.uuid4().hex[:10].upper()}'
        super().save(*args, **kwargs)


class OrderItem(models.Model):

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    # Which specific variant was purchased, if the product has
    # variants. Null for simple products (no variants).
    # PROTECT — an order item is permanent purchase history;
    # a variant should never be deleted while orders reference it.
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='order_items'
    )

    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['order']),
            models.Index(fields=['variant'], name='orderitem_variant_idx'),
        ]

    def __str__(self):
        if self.variant:
            return f'{self.variant.display_name} x {self.quantity}'
        return f'{self.product.name} x {self.quantity}'

    def save(self, *args, **kwargs):
        # Price is set explicitly by the checkout view (from cart
        # data, which already reflects variant.final_price if a
        # variant was selected). Only fall back to product.price
        # if no price was ever provided — defensive default.
        if not self.price:
            self.price = self.variant.final_price if self.variant else self.product.price
        super().save(*args, **kwargs)