from decimal import Decimal

from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.db.models.signals import post_save
from django.dispatch import receiver


class Business(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='businesses'
    )

    name = models.CharField(max_length=250)
    slug = models.SlugField(unique=True)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    industry = models.CharField(max_length=150)
    address = models.CharField(max_length=250)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100)

    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            'Optional vendor-specific commission rate. '
            'Overrides category and platform default. '
            '0.07 = 7%. Leave blank to use category or platform rate.'
        )
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Wallet(models.Model):

    business = models.OneToOneField(
        Business,
        on_delete=models.CASCADE,
        related_name='wallet'
    )

    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )

    escrow_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )

    currency = models.CharField(max_length=10, default='NGN')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def total_balance(self):
        return self.balance + self.escrow_balance

    def __str__(self):
        return (
            f'{self.business.name} Wallet | '
            f'Available: N{self.balance:,.2f} | '
            f'Escrow: N{self.escrow_balance:,.2f}'
        )


class BankAccount(models.Model):

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name='bank_accounts'
    )

    account_name = models.CharField(max_length=255)
    account_number = models.CharField(max_length=10)
    bank_name = models.CharField(max_length=255)
    bank_code = models.CharField(max_length=20)

    recipient_code = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
    )

    is_verified = models.BooleanField(default=False)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', '-created_at']
        indexes = [
            models.Index(
                fields=['business', 'is_active'],
                name='bankacct_active_idx'
            ),
        ]

    def __str__(self):
        return (
            f'{self.account_name} - {self.bank_name} '
            f'****{self.account_number[-4:]} '
            f'({"Primary" if self.is_primary else "Secondary"})'
        )

    def save(self, *args, **kwargs):
        if self.is_primary:
            BankAccount.objects.filter(
                business=self.business,
                is_primary=True,
            ).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)


class WithdrawalRequest(models.Model):

    STATUS_CHOICES = (
        ('pending_audit',    'Pending Audit'),
        ('audit_failed',     'Audit Failed'),
        ('pending_hold',     'Pending Hold Period'),
        ('pending_approval', 'Pending Approval'),
        ('approved',         'Approved'),
        ('processing',       'Processing'),
        ('completed',        'Completed'),
        ('failed',           'Failed'),
        ('rejected',         'Rejected'),
    )

    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name='withdrawal_requests'
    )

    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.SET_NULL,
        null=True,
        related_name='withdrawal_requests'
    )

    transaction = models.OneToOneField(
        'transactions.Transaction',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='withdrawal_request'
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending_audit'
    )

    hold_expires_at = models.DateTimeField(null=True, blank=True)
    audit_passed = models.BooleanField(null=True, blank=True)
    audit_notes = models.TextField(blank=True, null=True)
    requires_admin_review = models.BooleanField(default=False)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_withdrawals'
    )

    review_notes = models.TextField(blank=True, null=True)

    transfer_code = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True
    )

    rejection_reason = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['business', '-created_at'],
                name='wdl_business_date_idx'
            ),
            models.Index(fields=['status'], name='wdl_status_idx'),
            models.Index(
                fields=['status', 'hold_expires_at'],
                name='wdl_hold_expiry_idx'
            ),
        ]

    def __str__(self):
        return (
            f'Withdrawal N{self.amount:,.2f} - '
            f'{self.business.name} - '
            f'{self.get_status_display()}'
        )

    @property
    def is_cancellable(self):
        return self.status in ('pending_audit', 'pending_hold')

    @property
    def is_editable(self):
        return self.status == 'pending_audit'


# ============================================================
# VENDOR STOREFRONT MODEL (Phase 10 Step 5)
#
# Stores customisation data for a vendor's public-facing
# storefront page at /store/<business-slug>/.
# OneToOne with Business — every business gets exactly one
# storefront configuration.
# Auto-created via signal when a Business is created so every
# vendor always has a storefront record, even if uncustomised.
# ============================================================
class VendorStorefront(models.Model):

    business = models.OneToOneField(
        Business,
        on_delete=models.CASCADE,
        related_name='storefront'
    )

    # Short tagline shown under the business name on the storefront
    tagline = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text='Short description shown under your store name.'
    )

    # Accent colour as a hex code (e.g. #3b82f6).
    # Used for the storefront's header background and button colours.
    accent_colour = models.CharField(
        max_length=7,
        default='#3b82f6',
        help_text='Hex colour code for your storefront theme, e.g. #3b82f6.'
    )

    # Banner image — optional, shown at the top of the storefront.
    # ImageField stores the file path; actual file upload handled
    # by Cloudinary in production (same pattern as URC project).
    banner_image = models.ImageField(
        upload_to='storefront_banners/',
        blank=True,
        null=True,
        help_text='Recommended size: 1200 x 300px.'
    )

    # Vendor hand-picks up to 6 products to feature prominently
    # at the top of their storefront, above the full catalogue.
    # ManyToMany — blank=True since featuring products is optional.
    featured_products = models.ManyToManyField(
        'products.Product',
        blank=True,
        related_name='featured_in_storefronts',
        help_text='Select up to 6 products to feature on your storefront.'
    )

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Storefront — {self.business.name}'

    @property
    def has_banner(self):
        return bool(self.banner_image)

    @property
    def display_tagline(self):
        return self.tagline or self.business.industry


@receiver(post_save, sender=Business)
def create_business_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(business=instance)
        VendorStorefront.objects.create(business=instance)