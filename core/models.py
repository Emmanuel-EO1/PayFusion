import logging

from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger('payfusion')


class PlatformConfig(models.Model):

    # ----------------------------------------------------------
    # COMMISSION SETTINGS
    # Platform-wide default. Overridden by category rate,
    # then further overridden by vendor rate (most specific wins).
    # ----------------------------------------------------------
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=Decimal('0.10'),
        help_text='Platform default commission rate. 0.10 = 10%.'
    )

    # ----------------------------------------------------------
    # ESCROW SETTINGS
    # Controls when vendor funds are released from escrow
    # to their available (withdrawable) balance.
    # ----------------------------------------------------------

    # When True: funds released to vendor only after customer
    # confirms delivery. When False: funds released immediately
    # on payment confirmation (old behaviour).
    escrow_enabled = models.BooleanField(
        default=True,
        help_text=(
            'When enabled, vendor funds are held in escrow until '
            'the customer confirms delivery. '
            'Disabling releases funds immediately on payment.'
        )
    )

    # ----------------------------------------------------------
    # DELIVERY TIMEFRAMES
    # All enforced by Celery scheduled tasks in Phase 12.
    # Stored here so admin can adjust without redeploying.
    # ----------------------------------------------------------

    # How many days vendor has to accept a paid order
    # before the system auto-cancels and refunds the customer
    order_acceptance_days = models.PositiveIntegerField(
        default=2,
        help_text=(
            'Days vendor has to accept a paid order. '
            'After this, order is auto-cancelled and customer refunded.'
        )
    )

    # How many days vendor has to ship after accepting
    order_shipping_days = models.PositiveIntegerField(
        default=3,
        help_text=(
            'Days vendor has to ship after accepting. '
            'After this, order is auto-cancelled and customer refunded.'
        )
    )

    # How many days before an in_transit order is auto-escalated
    # to admin if customer has not confirmed delivery
    delivery_timeout_days = models.PositiveIntegerField(
        default=14,
        help_text=(
            'Days before an in_transit order is flagged for admin '
            'review if customer has not confirmed delivery.'
        )
    )

    # How many days after delivery the customer has to confirm.
    # After this, delivery is auto-confirmed and escrow released.
    # Protects vendors from customers who never click confirm.
    customer_confirm_days = models.PositiveIntegerField(
        default=7,
        help_text=(
            'Days after shipping that customer has to confirm delivery. '
            'After this, delivery is auto-confirmed and escrow released.'
        )
    )

    # ----------------------------------------------------------
    # DISPUTE TIMEFRAMES
    # ----------------------------------------------------------

    # How many days after delivery confirmation a customer
    # can raise a dispute. After this window, dispute option
    # is removed from the customer's order page.
    dispute_window_days = models.PositiveIntegerField(
        default=3,
        help_text=(
            'Days after delivery confirmation that customer can '
            'raise a dispute. After this window, disputes are closed.'
        )
    )

    # How many days admin has to resolve an open dispute
    # before it is auto-escalated to senior admin
    dispute_resolution_days = models.PositiveIntegerField(
        default=7,
        help_text=(
            'Days admin has to resolve a dispute before '
            'it is auto-escalated.'
        )
    )

    # ----------------------------------------------------------
    # WITHDRAWAL POLICY SETTINGS
    # ----------------------------------------------------------
    withdrawal_hold_days = models.PositiveIntegerField(
        default=7,
        help_text='Days funds must be held after escrow release before withdrawal.'
    )

    minimum_withdrawal_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('500.00'),
        help_text='Minimum withdrawal amount in Naira.'
    )

    auto_approval_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('500000.00'),
        help_text='Withdrawals at or above this require manual admin approval.'
    )

    # ----------------------------------------------------------
    # FRAUD DETECTION SETTINGS
    # ----------------------------------------------------------
    fraud_failed_tx_threshold = models.PositiveIntegerField(
        default=3,
        help_text='Failed transactions within lookback window that triggers fraud flag.'
    )

    fraud_lookback_hours = models.PositiveIntegerField(
        default=24,
        help_text='Hours to look back when checking for suspicious activity.'
    )

    # ----------------------------------------------------------
    # METADATA
    # ----------------------------------------------------------
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='platform_config_updates',
    )

    class Meta:
        verbose_name = 'Platform Configuration'
        verbose_name_plural = 'Platform Configuration'

    def __str__(self):
        return (
            f'PayFusion Config | '
            f'Commission: {int(self.commission_rate * 100)}% | '
            f'Hold: {self.withdrawal_hold_days}d | '
            f'Escrow: {"ON" if self.escrow_enabled else "OFF"}'
        )

    def clean(self):
        if not self.pk and PlatformConfig.objects.exists():
            raise ValidationError(
                'Only one Platform Configuration record is allowed. '
                'Edit the existing record instead of creating a new one.'
            )

        if not (Decimal('0') <= self.commission_rate <= Decimal('1')):
            raise ValidationError(
                'Commission rate must be between 0 and 1. Example: 0.10 for 10%.'
            )

        if self.minimum_withdrawal_amount <= 0:
            raise ValidationError(
                'Minimum withdrawal amount must be greater than zero.'
            )

        if self.auto_approval_threshold <= self.minimum_withdrawal_amount:
            raise ValidationError(
                'Auto-approval threshold must be greater than minimum withdrawal amount.'
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        config = cls.objects.first()
        if config is None:
            logger.warning(
                'PlatformConfig not found — using defaults. '
                'Please create a configuration record in the admin panel.'
            )
            config = cls()
        return config