from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.db.models.signals import post_save
from django.dispatch import receiver


# ============================================================
# BUSINESS (TENANT) MODEL
# Represents a merchant operating on the PayFusion platform.
# One user can own multiple businesses.
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
# The virtual financial account of each business.
# Balance is updated exclusively through services.py —
# never modified directly.
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
        return f'{self.business.name} Wallet — ₦{self.balance:,.2f}'


# ============================================================
# BANK ACCOUNT MODEL
# Stores a vendor's bank account details for payouts.
#
# Design decisions:
#   - A vendor can have multiple bank accounts
#   - Only one can be primary (is_primary=True) at a time
#   - is_active uses soft delete — bank records are never
#     hard deleted because they are referenced by withdrawal
#     history. Deactivating keeps the audit trail intact.
#   - recipient_code is assigned by Paystack after we register
#     the account via create_transfer_recipient(). It is what
#     we pass to initiate_transfer() during payouts.
#   - is_verified means we called resolve_bank_account() and
#     Paystack confirmed the account exists and returned the
#     registered account holder name.
# ============================================================
class BankAccount(models.Model):

    # The business this bank account belongs to
    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name='bank_accounts'
    )

    # Account holder name — returned by Paystack's resolve endpoint
    # Always populated from Paystack, never typed manually by vendor
    account_name = models.CharField(max_length=255)

    # 10-digit Nigerian NUBAN account number
    account_number = models.CharField(max_length=10)

    # Bank display name e.g. "Guaranty Trust Bank"
    bank_name = models.CharField(max_length=255)

    # Paystack bank code e.g. "058" for GTBank
    # Used when creating transfer recipients and initiating transfers
    bank_code = models.CharField(max_length=20)

    # Paystack Transfer Recipient code e.g. "RCP_abc123xyz"
    # Assigned by Paystack after create_transfer_recipient() is called
    # Null until the account is registered with Paystack
    recipient_code = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
    )

    # True if Paystack's resolve endpoint confirmed this account exists
    # and returned the registered account holder name
    is_verified = models.BooleanField(default=False)

    # True if this is the vendor's primary withdrawal account
    # Only one bank account per business can be primary at a time
    # Enforced in the save() method below
    is_primary = models.BooleanField(default=False)

    # Soft delete — deactivated accounts are hidden from the vendor
    # but preserved in the database for withdrawal history audit trail
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', '-created_at']
        indexes = [
            # Fast lookup of all accounts for a business
            models.Index(
                fields=['business', 'is_active'],
                name='bankacct_active_idx'
            ),
        ]

    def __str__(self):
        return (
            f'{self.account_name} — {self.bank_name} '
            f'****{self.account_number[-4:]} '
            f'({"Primary" if self.is_primary else "Secondary"})'
        )

    def save(self, *args, **kwargs):
        # Enforce single primary account per business.
        # If this account is being set as primary, demote all
        # other accounts for the same business first.
        if self.is_primary:
            BankAccount.objects.filter(
                business=self.business,
                is_primary=True,
            ).exclude(pk=self.pk).update(is_primary=False)

        super().save(*args, **kwargs)


# ============================================================
# SIGNAL — Auto-create wallet on business creation
# Every new business immediately gets a wallet with zero balance
# ============================================================
@receiver(post_save, sender=Business)
def create_business_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(business=instance)