import logging

from django.db import models
from django.conf import settings
from tenants.models import Business
from django.utils.crypto import get_random_string

logger = logging.getLogger('payfusion')


# ============================================================
# REFERENCE GENERATOR
# ============================================================
def generate_transaction_reference():
    return 'PAYFUSION_' + get_random_string(12).upper()


# ============================================================
# TRANSACTION MODEL
# The immutable financial record of every money movement.
# Never modify balances here — that is the job of services.py
# ============================================================
class Transaction(models.Model):

    TRANSACTION_TYPES = (
        ('credit', 'Credit'),
        ('debit', 'Debit'),
        ('payment', 'Payment'),         # Master checkout transaction (Treasury)
        ('withdrawal', 'Withdrawal'),   # Vendor payout request
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
            # Fast lookups by reference (used on every webhook + callback)
            models.Index(fields=['reference'], name='txn_reference_idx'),
            # Fast lookups by user (transaction history pages)
            models.Index(fields=['user', '-created_at'], name='txn_user_date_idx'),
            # Fast lookups by business (vendor dashboard)
            models.Index(fields=['business', '-created_at'], name='txn_business_date_idx'),
            # Fast status filtering
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
        # Model is an immutable historical record.
        # Balance updates happen exclusively in services.py
        super().save(*args, **kwargs)


# ============================================================
# WEBHOOK EVENT MODEL
# Logs every raw Paystack webhook payload permanently.
#
# Why this exists:
#   - If handler crashes mid-processing, the raw event is preserved
#   - Enables manual replay of any failed event
#   - Provides proof of receipt if Paystack disputes delivery
#   - Catches and blocks duplicate events (Paystack retries on failure)
#   - Essential audit trail for a fintech platform
# ============================================================
class WebhookEvent(models.Model):

    STATUS_CHOICES = (
        ('received', 'Received'),       # Event logged but not yet processed
        ('processed', 'Processed'),     # Successfully handled
        ('failed', 'Failed'),           # Handler crashed or returned error
        ('duplicate', 'Duplicate'),     # Already processed — safely ignored
        ('ignored', 'Ignored'),         # Unrecognised event type — safely skipped
    )

    # The unique Paystack event ID (from payload id field if present)
    # Used to detect and block duplicate deliveries
    event_id = models.CharField(max_length=100, unique=True, null=True, blank=True)

    # The event type e.g. charge.success, transfer.success, transfer.failed
    event_type = models.CharField(max_length=100)

    # The transaction reference from the event payload
    reference = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    # Full raw JSON payload from Paystack — never modified, always complete
    payload = models.JSONField()

    # Processing status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')

    # If processing failed, store the error message here for debugging
    error_message = models.TextField(blank=True, null=True)

    # Timestamps
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