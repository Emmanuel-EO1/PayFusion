from django.db import models
from django.conf import settings
from products.models import Product
from tenants.models import Business
from transactions.models import Transaction
import uuid

class Order(models.Model):

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    )

    # --- Core Relationships ---
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='orders')

    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='orders')

    # --- Financials ---
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    # --- Metadata ---
    reference = models.CharField(max_length=50, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
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

    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['order']),
        ]

    def __str__(self):
        return f'{self.product.name} x {self.quantity}'
    
    def save(self, *args, **kwargs):
        if not self.price:
            self.price = self.product.price
        super().save(*args, **kwargs)
