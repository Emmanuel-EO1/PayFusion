import logging

from django.db import models
from django.conf import settings
from tenants.models import Business
from django.utils.crypto import get_random_string

logger = logging.getLogger('payfusion')


def generate_transaction_reference():
    return 'PAYFUSION_' + get_random_string(12).upper()


class Transaction(models.Model):

    TRANSACTION_TYPES = (
        ('credit', 'Credit'),
        ('debit', 'Debit'),
        ('payment', 'Payment'),
        ('withdrawal', 'Withdrawal'),
    )

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = models.CharField(max_length=15, choices=TRANSACTION_TYPES)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    reference = models.CharField(
        max_length=50,
        unique=True,
        default=generate_transaction_reference,
        editable=False
    )
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['reference'], name='txn_reference_idx'),
            models.Index(fields=['user', '-created_at'], name='txn_user_date_idx'),
            models.Index(fields=['business', '-created_at'], name='txn_business_date_idx'),
            models.Index(fields=['status'], name='txn_status_idx'),
        ]

    def __str__(self):
        return (
            f"{self.transaction_type.capitalize()} | "
            f"{self.amount} | "
            f"{self.reference} | "
            f"{self.business.name}"
        )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)


class WebhookEvent(models.Model):

    STATUS_CHOICES = (
        ('received', 'Received'),
        ('processed', 'Processed'),
        ('failed', 'Failed'),
        ('duplicate', 'Duplicate'),
        ('ignored', 'Ignored'),
    )

    event_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    event_type = models.CharField(max_length=100)
    reference = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    payload = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    error_message = models.TextField(blank=True, null=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['event_id'], name='webhook_event_id_idx'),
            models.Index(fields=['reference'], name='webhook_reference_idx'),
            models.Index(fields=['status'], name='webhook_status_idx'),
            models.Index(fields=['event_type'], name='webhook_event_type_idx'),
        ]

    def __str__(self):
        return f"{self.event_type} | {self.reference} | {self.status}"


# ============================================================
# ESCROW ENTRY MODEL
#
# Records every escrow movement for every vendor order.
# Two entry types exist:
#   hold    → funds moved into escrow on payment confirmation
#   release → funds moved from escrow to available balance
#             on delivery confirmation (or auto-confirmation)
#
# Why this exists separate from Transaction:
#   Transaction records money movement between parties
#   (customer → treasury, treasury → vendor wallet).
#   EscrowEntry records the internal lifecycle of those funds
#   within the vendor's wallet — held vs available.
#   These are different concerns requiring separate records.
#
# is_frozen:
#   Set to True when a dispute is raised on the linked order.
#   Frozen entries cannot be released — funds stay locked
#   until the dispute is resolved by admin.
#   This prevents vendors from having escrow auto-released
#   while a customer dispute is still open.
# ============================================================
class EscrowEntry(models.Model):

    ENTRY_TYPES = (
        ('hold',    'Hold'),     # Funds moved into escrow
        ('release', 'Release'),  # Funds released to available balance
    )

    # The vendor's business this escrow entry belongs to
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name='escrow_entries'
    )

    # The order that triggered this escrow movement
    # PROTECT — never delete an order that has escrow entries
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='escrow_entries'
    )

    # The master payment transaction this escrow entry relates to
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.PROTECT,
        related_name='escrow_entries'
    )

    # hold: funds entering escrow on payment confirmation
    # release: funds leaving escrow to available balance
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPES)

    # Amount in Naira — always positive regardless of direction
    # Direction is determined by entry_type
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    # Frozen when a dispute is raised on the linked order.
    # Frozen entries cannot be auto-released by Celery.
    # Only unfrozen by admin when dispute is resolved.
    is_frozen = models.BooleanField(default=False)

    # Why this entry was created — useful for admin debugging
    description = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # Fast lookup of all escrow entries for a business
            models.Index(
                fields=['business', '-created_at'],
                name='escrow_biz_date_idx'
            ),
            # Fast lookup by order — used when releasing escrow
            # on delivery confirmation and when freezing on dispute
            models.Index(
                fields=['order'],
                name='escrow_order_idx'
            ),
            # Fast lookup of frozen entries — used by admin
            # and dispute resolution service
            models.Index(
                fields=['is_frozen'],
                name='escrow_frozen_idx'
            ),
        ]

    def __str__(self):
        return (
            f'Escrow {self.entry_type.capitalize()} | '
            f'{self.business.name} | '
            f'N{self.amount:,.2f} | '
            f'Order: {self.order.reference}'
        )