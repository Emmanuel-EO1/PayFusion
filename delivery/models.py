import logging

from django.db import models
from django.conf import settings
from orders.models import Order

logger = logging.getLogger('payfusion')


class Delivery(models.Model):

    STATUS_CHOICES = (
        ('pending',    'Pending'),
        ('processing', 'Processing'),
        ('shipped',    'Shipped'),
        ('in_transit', 'In Transit'),
        ('delivered',  'Delivered'),
        ('cancelled',  'Cancelled'),
        ('returned',   'Returned'),
    )

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name='delivery'
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
    )

    tracking_id = models.CharField(max_length=100, blank=True, null=True)
    courier = models.CharField(max_length=100, blank=True, null=True)

    # True when vendor is doing hand delivery.
    # Controls who can mark delivered:
    #   True  → vendor can mark delivered
    #   False → only customer can confirm delivery
    is_hand_delivery = models.BooleanField(default=False)

    estimated_delivery_date = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    accepted_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status'], name='delivery_status_idx'),
            models.Index(fields=['order'], name='delivery_order_idx'),
        ]

    def __str__(self):
        return (
            f'Delivery for Order {self.order.reference} — '
            f'{self.get_status_display()}'
        )

    @property
    def is_active(self):
        return self.status not in ('delivered', 'cancelled', 'returned')

    @property
    def customer(self):
        return self.order.user

    @property
    def vendor_business(self):
        return self.order.business


class DeliveryStatusHistory(models.Model):

    delivery = models.ForeignKey(
        Delivery,
        on_delete=models.CASCADE,
        related_name='history'
    )

    status = models.CharField(
        max_length=20,
        choices=Delivery.STATUS_CHOICES,
    )

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    note = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']
        indexes = [
            models.Index(
                fields=['delivery', 'timestamp'],
                name='delivery_history_idx'
            ),
        ]

    def __str__(self):
        return (
            f'{self.delivery.order.reference} — '
            f'{self.get_status_display()} — '
            f'{self.timestamp.strftime("%b %d %H:%M")}'
        )