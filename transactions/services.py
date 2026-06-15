import logging

from decimal import Decimal
from django.db import transaction

from tenants.models import Wallet
from transactions.models import Transaction, EscrowEntry, generate_transaction_reference

logger = logging.getLogger('payfusion')


# ============================================================
# COMMISSION RATE RESOLVER
# ============================================================

def get_commission_rate(business, product=None):
    """
    Resolves the correct commission rate for a vendor order item.
    Three-tier hierarchy — most specific wins:
      1. Vendor rate (Business.commission_rate)
      2. Category rate (ProductCategory.commission_rate)
      3. Platform default (PlatformConfig.commission_rate)
    """
    from core.models import PlatformConfig

    # Tier 1 — vendor-specific rate
    if business.commission_rate is not None:
        logger.info(
            f'Commission: using vendor rate {business.commission_rate} '
            f'for {business.name}'
        )
        return business.commission_rate

    # Tier 2 — category rate
    if product and product.category and product.category.commission_rate is not None:
        logger.info(
            f'Commission: using category rate {product.category.commission_rate} '
            f'for category {product.category.name}'
        )
        return product.category.commission_rate

    # Tier 3 — platform default
    config = PlatformConfig.get_config()
    logger.info(
        f'Commission: using platform default {config.commission_rate}'
    )
    return config.commission_rate


# ============================================================
# CREDIT WALLET
# Adds funds to a business available balance.
# Used for: Treasury collection, vendor settlement,
#           escrow release, failed transfer reversal.
# ============================================================

def credit_wallet(business, amount, user=None, description=''):
    """
    Credit a business available (withdrawable) wallet balance.
    Creates a ledger Transaction entry for audit trail.
    """
    with transaction.atomic():

        wallet, _ = Wallet.objects.select_for_update().get_or_create(
            business=business,
            defaults={'balance': Decimal('0.00')}
        )

        amount_decimal = Decimal(str(amount))
        wallet.balance = wallet.balance + amount_decimal
        wallet.save(update_fields=['balance', 'updated_at'])

        Transaction.objects.create(
            user=user,
            business=business,
            amount=amount_decimal,
            transaction_type='credit',
            status='completed',
            reference=generate_transaction_reference(),
            description=description,
        )

        logger.info(
            f'Wallet credited — business: {business.name} | '
            f'amount: N{amount_decimal} | new balance: N{wallet.balance}'
        )


# ============================================================
# DEBIT WALLET
# Removes funds from a business available balance.
# Used for: Treasury payouts, withdrawal holds.
# Raises ValueError on insufficient funds.
# ============================================================

def debit_wallet(business, amount, user=None, description=''):
    """
    Debit a business available (withdrawable) wallet balance.
    Creates a ledger Transaction entry for audit trail.
    Raises ValueError if balance is insufficient.
    """
    with transaction.atomic():

        wallet, _ = Wallet.objects.select_for_update().get_or_create(
            business=business,
            defaults={'balance': Decimal('0.00')}
        )

        amount_decimal = Decimal(str(amount))

        if wallet.balance < amount_decimal:
            logger.warning(
                f'Insufficient funds — business: {business.name} | '
                f'requested: N{amount_decimal} | available: N{wallet.balance}'
            )
            raise ValueError(
                f'Insufficient funds for business: {business.name}. '
                f'Available: N{wallet.balance}, Requested: N{amount_decimal}'
            )

        wallet.balance = wallet.balance - amount_decimal
        wallet.save(update_fields=['balance', 'updated_at'])

        Transaction.objects.create(
            user=user,
            business=business,
            amount=amount_decimal,
            transaction_type='debit',
            status='completed',
            reference=generate_transaction_reference(),
            description=description,
        )

        logger.info(
            f'Wallet debited — business: {business.name} | '
            f'amount: N{amount_decimal} | new balance: N{wallet.balance}'
        )


# ============================================================
# CREDIT ESCROW
# Holds vendor funds in escrow on payment confirmation.
# Increases escrow_balance — does NOT touch available balance.
# Creates EscrowEntry(hold) for audit trail.
# Called by: webhook handler on charge.success
# ============================================================

def credit_escrow(business, order, txn, amount, description=''):
    """
    Hold vendor settlement funds in escrow.
    Funds are visible as escrow_balance but not withdrawable.
    Released to available balance when delivery is confirmed.

    Args:
        business:    Vendor Business instance
        order:       The Order being held in escrow
        txn:         The master payment Transaction
        amount:      Amount in Naira (Decimal)
        description: Audit trail description
    """
    with transaction.atomic():

        wallet = Wallet.objects.select_for_update().get(business=business)

        amount_decimal = Decimal(str(amount))
        wallet.escrow_balance = wallet.escrow_balance + amount_decimal
        wallet.save(update_fields=['escrow_balance', 'updated_at'])

        EscrowEntry.objects.create(
            business=business,
            order=order,
            transaction=txn,
            entry_type='hold',
            amount=amount_decimal,
            description=description or (
                f'Escrow hold for order {order.reference}'
            ),
        )

        logger.info(
            f'Escrow credited — business: {business.name} | '
            f'order: {order.reference} | '
            f'amount: N{amount_decimal} | '
            f'escrow_balance: N{wallet.escrow_balance}'
        )


# ============================================================
# RELEASE ESCROW
# Releases held funds to vendor available balance on delivery.
# Decreases escrow_balance, increases balance.
# Creates EscrowEntry(release) for audit trail.
# Called by: delivery confirmation signal
# Blocked if escrow entry is frozen (active dispute).
# ============================================================

def release_escrow(order):
    """
    Release escrow funds to vendor available balance.
    Called when customer confirms delivery or auto-confirmation fires.
    Blocked silently if escrow is frozen due to an active dispute.

    Args:
        order: The Order whose escrow should be released
    """
    try:
        escrow_entry = EscrowEntry.objects.get(
            order=order,
            entry_type='hold',
        )
    except EscrowEntry.DoesNotExist:
        logger.warning(
            f'Release escrow: no hold entry found for order {order.reference}'
        )
        return

    # Frozen entries cannot be released — dispute is active
    if escrow_entry.is_frozen:
        logger.info(
            f'Release escrow blocked — order {order.reference} '
            f'escrow is frozen due to active dispute'
        )
        return

    business = escrow_entry.business
    amount = escrow_entry.amount

    with transaction.atomic():

        wallet = Wallet.objects.select_for_update().get(business=business)

        # Safety check — escrow_balance should always cover this
        # but we validate to prevent negative escrow
        if wallet.escrow_balance < amount:
            logger.error(
                f'Release escrow error — escrow_balance ({wallet.escrow_balance}) '
                f'less than release amount ({amount}) '
                f'for order {order.reference}'
            )
            return

        wallet.escrow_balance = wallet.escrow_balance - amount
        wallet.balance = wallet.balance + amount
        wallet.save(update_fields=['escrow_balance', 'balance', 'updated_at'])

        EscrowEntry.objects.create(
            business=business,
            order=order,
            transaction=escrow_entry.transaction,
            entry_type='release',
            amount=amount,
            description=f'Escrow released on delivery confirmation — order {order.reference}',
        )

        logger.info(
            f'Escrow released — business: {business.name} | '
            f'order: {order.reference} | '
            f'amount: N{amount} | '
            f'new available balance: N{wallet.balance}'
        )


# ============================================================
# FREEZE ESCROW
# Freezes escrow when a dispute is raised on an order.
# Prevents auto-release until dispute is resolved.
# Called by: dispute creation signal (Phase 11)
# ============================================================

def freeze_escrow(order):
    """
    Freeze escrow for an order when a dispute is raised.
    Frozen escrow cannot be auto-released by Celery or
    manually released until the dispute is resolved.

    Args:
        order: The Order whose escrow should be frozen
    """
    updated = EscrowEntry.objects.filter(
        order=order,
        entry_type='hold',
        is_frozen=False,
    ).update(is_frozen=True)

    if updated:
        logger.info(
            f'Escrow frozen — order: {order.reference} | '
            f'dispute raised'
        )
    else:
        logger.warning(
            f'Freeze escrow: no unfrozen hold entry found '
            f'for order {order.reference}'
        )


# ============================================================
# UNFREEZE ESCROW
# Unfreezes escrow when a dispute resolves in vendor's favour.
# After unfreezing, release_escrow is called immediately
# to move funds to vendor's available balance.
# Called by: dispute resolution in admin (Phase 11)
# ============================================================

def unfreeze_and_release_escrow(order):
    """
    Unfreeze escrow and immediately release to vendor balance.
    Called when a dispute is resolved in the vendor's favour.
    If resolved in customer's favour, use process_refund() instead.

    Args:
        order: The Order whose escrow should be unfrozen and released
    """
    updated = EscrowEntry.objects.filter(
        order=order,
        entry_type='hold',
        is_frozen=True,
    ).update(is_frozen=False)

    if updated:
        logger.info(
            f'Escrow unfrozen — order: {order.reference} | '
            f'dispute resolved in vendor favour'
        )
        release_escrow(order)
    else:
        logger.warning(
            f'Unfreeze escrow: no frozen hold entry found '
            f'for order {order.reference}'
        )