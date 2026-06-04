from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.db.models.signals import post_save
from django.dispatch import receiver


# ============================================================
# BUSINESS (TENANT) MODEL
# ============================================================
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


# ============================================================
# WALLET MODEL
# ============================================================
class Wallet(models.Model):

    business = models.OneToOneField(
        Business,
        on_delete=models.CASCADE,
        related_name='wallet'
    )

    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0.00
    )

    currency = models.CharField(max_length=10, default='NGN')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.business.name} Wallet - N{self.balance:,.2f}'


# ============================================================
# BANK ACCOUNT MODEL
# ============================================================
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


# ============================================================
# WITHDRAWAL REQUEST MODEL
#
# Sits between the vendor's withdrawal request and the actual
# Paystack bank transfer. Every withdrawal goes through this
# model's pipeline before real money moves.
#
# Pipeline:
#   pending_audit     -> audit running
#   audit_failed      -> blocked (dispute, fraud flag, etc.)
#   pending_hold      -> audit passed, hold period not expired
#   pending_approval  -> hold expired, awaiting approval
#   approved          -> all gates passed, ready to transfer
#   processing        -> Paystack transfer API called
#   completed         -> transfer.success confirmed by webhook
#   failed            -> transfer.failed - funds auto-reversed
#   rejected          -> manually rejected by admin
# ============================================================
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

    # The vendor's business making the request
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name='withdrawal_requests'
    )

    # The bank account to pay into
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.SET_NULL,
        null=True,
        related_name='withdrawal_requests'
    )

    # The withdrawal transaction record (debit from vendor wallet)
    transaction = models.OneToOneField(
        'transactions.Transaction',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='withdrawal_request'
    )

    # Amount requested in Naira
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    # Current status in the pipeline
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending_audit'
    )

    # When the hold period expires and funds become eligible
    hold_expires_at = models.DateTimeField(null=True, blank=True)

    # True if all automated audit checks passed
    audit_passed = models.BooleanField(null=True, blank=True)

    # Detailed notes from the audit engine
    audit_notes = models.TextField(blank=True, null=True)

    # True if flagged for manual admin review
    requires_admin_review = models.BooleanField(default=False)

    # Which admin reviewed this request
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_withdrawals'
    )

    # Admin notes on their decision
    review_notes = models.TextField(blank=True, null=True)

    # TRF_xxx code from Paystack when transfer is initiated
    transfer_code = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True
    )

    # Shown to vendor when request is rejected or blocked
    rejection_reason = models.TextField(blank=True, null=True)

    # Timestamps
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
            models.Index(
                fields=['status'],
                name='wdl_status_idx'
            ),
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
        """
        A withdrawal can only be cancelled while it is still
        in audit or hold period - before any money has moved.
        """
        return self.status in ('pending_audit', 'pending_hold')

    @property
    def is_editable(self):
        """
        Only pending_audit requests can be modified.
        """
        return self.status == 'pending_audit'


# ============================================================
# SIGNAL - Auto-create wallet on business creation
# ============================================================
@receiver(post_save, sender=Business)
def create_business_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(business=instance)