import logging

from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger('payfusion')


# ============================================================
# PLATFORM CONFIG MODEL
#
# A single-row configuration table that stores all platform-wide
# policy parameters. Admins update these from the admin panel
# without touching code or redeploying.
#
# Parameters covered:
#   Payment & Commission
#     - Platform commission rate
#
#   Withdrawal Policy
#     - Hold period days
#     - Minimum withdrawal amount
#     - Auto-approval threshold
#
#   Fraud Detection
#     - Failed transaction threshold
#     - Fraud lookback hours
#
# SINGLE ROW DESIGN:
#   This table holds exactly one row.
#   Access config anywhere via PlatformConfig.get_config()
# ============================================================
class PlatformConfig(models.Model):

    # ----------------------------------------------------------
    # COMMISSION SETTINGS
    # ----------------------------------------------------------
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=Decimal('0.10'),
        help_text=(
            'Platform commission rate as a decimal. '
            '0.10 = 10%, 0.05 = 5%. '
            'Applied to every vendor settlement.'
        )
    )

    # ----------------------------------------------------------
    # WITHDRAWAL POLICY SETTINGS
    # ----------------------------------------------------------
    withdrawal_hold_days = models.PositiveIntegerField(
        default=7,
        help_text=(
            'Number of days funds must be held after a sale '
            'before a vendor can withdraw. '
            'Industry standard is 7 days.'
        )
    )

    minimum_withdrawal_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('500.00'),
        help_text=(
            'Minimum withdrawal amount in Naira. '
            'Prevents micro-withdrawals that incur unnecessary transfer fees.'
        )
    )

    auto_approval_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('500000.00'),
        help_text=(
            'Withdrawals at or above this amount (Naira) require '
            'manual admin approval before transfer is initiated. '
            'Default: ₦500,000.'
        )
    )

    # ----------------------------------------------------------
    # FRAUD DETECTION SETTINGS
    # ----------------------------------------------------------
    fraud_failed_tx_threshold = models.PositiveIntegerField(
        default=3,
        help_text=(
            'Number of failed transactions within the lookback window '
            'that flags a withdrawal for admin review. Default: 3.'
        )
    )

    fraud_lookback_hours = models.PositiveIntegerField(
        default=24,
        help_text=(
            'How many hours back to look when checking for '
            'suspicious failed transaction activity. Default: 24 hours.'
        )
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
        help_text='Last admin who updated the platform configuration.',
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
            f'Min withdrawal: N{self.minimum_withdrawal_amount:,.2f}'
        )

    def clean(self):
        # Enforce single-row constraint
        if not self.pk and PlatformConfig.objects.exists():
            raise ValidationError(
                'Only one Platform Configuration record is allowed. '
                'Edit the existing record instead of creating a new one.'
            )

        # Commission rate must be between 0 and 1
        if not (Decimal('0') <= self.commission_rate <= Decimal('1')):
            raise ValidationError(
                'Commission rate must be between 0 and 1. '
                'Example: 0.10 for 10%.'
            )

        # Minimum withdrawal must be positive
        if self.minimum_withdrawal_amount <= 0:
            raise ValidationError(
                'Minimum withdrawal amount must be greater than zero.'
            )

        # Auto approval threshold must be above minimum withdrawal
        if self.auto_approval_threshold <= self.minimum_withdrawal_amount:
            raise ValidationError(
                'Auto-approval threshold must be greater than '
                'the minimum withdrawal amount.'
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        """
        Fetch the platform configuration.
        Creates default instance if none exists.
        Always returns a valid config object — system never crashes.

        Usage anywhere in codebase:
            from core.models import PlatformConfig
            config = PlatformConfig.get_config()
            rate = config.commission_rate
        """
        config = cls.objects.first()

        if config is None:
            logger.warning(
                'PlatformConfig not found in database — using defaults. '
                'Please create a configuration record in the admin panel.'
            )
            config = cls()

        return config