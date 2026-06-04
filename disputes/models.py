import logging

from django.db import models
from django.conf import settings
from orders.models import Order
from tenants.models import Business

logger = logging.getLogger('payfusion')


# ============================================================
# DISPUTE MODEL
#
# Raised by a customer against a paid order.
# While a dispute is open or under review, the vendor
# cannot withdraw funds — the audit service enforces this.
#
# Status flow:
#   open          -> customer just raised it
#   under_review  -> admin is investigating
#   resolved      -> decision made (refund / no refund)
#   closed        -> finalised, no further action
#
# Resolution types:
#   refund_issued      -> customer refunded, funds debited from vendor
#   no_refund          -> dispute rejected, vendor keeps funds
#   partial_refund     -> partial amount returned to customer
#   escalated          -> referred to external body
# ============================================================
class Dispute(models.Model):

    STATUS_CHOICES = (
        ('open',         'Open'),
        ('under_review', 'Under Review'),
        ('resolved',     'Resolved'),
        ('closed',       'Closed'),
    )

    RESOLUTION_CHOICES = (
        ('refund_issued',   'Refund Issued'),
        ('no_refund',       'No Refund'),
        ('partial_refund',  'Partial Refund'),
        ('escalated',       'Escalated'),
    )

    REASON_CHOICES = (
        ('item_not_received',   'Item Not Received'),
        ('item_not_as_described', 'Item Not As Described'),
        ('wrong_item',          'Wrong Item Delivered'),
        ('damaged_item',        'Item Arrived Damaged'),
        ('unauthorized_charge', 'Unauthorized Charge'),
        ('duplicate_charge',    'Duplicate Charge'),
        ('other',               'Other'),
    )

    # The order being disputed
    # PROTECT — never delete an order that has a dispute
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name='disputes'
    )

    # The vendor whose business is being disputed against
    # Denormalised from order.business for faster audit queries
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name='disputes'
    )

    # The customer raising the dispute
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='raised_disputes'
    )

    # Categorised reason for the dispute
    reason = models.CharField(
        max_length=30,
        choices=REASON_CHOICES,
    )

    # Customer's detailed description of the issue
    description = models.TextField()

    # The amount the customer is disputing
    # May be less than the full order total (partial disputes)
    amount_disputed = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    # Current status in the dispute pipeline
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='open',
    )

    # How the dispute was resolved
    resolution = models.CharField(
        max_length=20,
        choices=RESOLUTION_CHOICES,
        blank=True,
        null=True,
    )

    # Admin notes on the resolution decision
    resolution_notes = models.TextField(blank=True, null=True)

    # Which admin resolved this dispute
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_disputes'
    )

    # Evidence uploaded by customer (Phase 11 — file uploads)
    # Placeholder for now — will be a FileField later
    evidence_note = models.TextField(
        blank=True,
        null=True,
        help_text='Customer-provided evidence description'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # Fast lookup of all disputes for a business
            # Used by audit service Check 3
            models.Index(
                fields=['business', 'status'],
                name='dispute_biz_status_idx'
            ),
            # Fast lookup of disputes for an order
            models.Index(
                fields=['order'],
                name='dispute_order_idx'
            ),
            # Fast lookup of disputes by customer
            models.Index(
                fields=['raised_by'],
                name='dispute_raised_by_idx'
            ),
        ]

    def __str__(self):
        return (
            f'Dispute #{self.id} — '
            f'Order #{self.order.id} — '
            f'{self.get_status_display()}'
        )

    @property
    def is_blocking(self):
        """
        Returns True if this dispute should block
        a vendor withdrawal.
        Only open and under_review disputes block withdrawals.
        Resolved and closed disputes do not.
        """
        return self.status in ('open', 'under_review')