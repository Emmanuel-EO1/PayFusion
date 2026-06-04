import logging

from decimal import Decimal
from django.db import transaction

from tenants.models import Wallet
from transactions.models import Transaction, generate_transaction_reference

logger = logging.getLogger('payfusion')


# ============================================================
# CREDIT WALLET
# Adds funds to a business wallet and logs a credit transaction.
#
# Called by:
#   - Webhook handler (crediting Treasury on customer payment)
#   - Webhook handler (crediting vendor on settlement)
#   - Webhook handler (reversing vendor on failed transfer)
#
# Always runs inside transaction.atomic() with select_for_update()
# to prevent race conditions when multiple webhooks arrive
# simultaneously or a checkout and webhook overlap.
# ============================================================
def credit_wallet(business, amount, user=None, description=''):
    """
    Credit a business wallet by the given amount.

    Args:
        business:    The Business instance whose wallet to credit
        amount:      Amount in Naira (Decimal or string)
        user:        The User associated with this transaction (optional)
        description: Human-readable description for the ledger entry
    """
    with transaction.atomic():

        # Lock the wallet row for the duration of this transaction
        # get_or_create handles the edge case where a wallet doesn't
        # exist yet (should not happen due to the signal, but safe)
        wallet, _ = Wallet.objects.select_for_update().get_or_create(
            business=business,
            defaults={'balance': Decimal('0.00')}
        )

        amount_decimal = Decimal(str(amount))

        # Update balance directly — no F() expressions here because
        # we already hold a row lock via select_for_update()
        # Using F() with a lock is redundant and can mask logic errors
        wallet.balance = wallet.balance + amount_decimal
        wallet.save(update_fields=['balance', 'updated_at'])

        # Log the ledger entry
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
            f'amount: ₦{amount_decimal} | new balance: ₦{wallet.balance}'
        )


# ============================================================
# DEBIT WALLET
# Removes funds from a business wallet and logs a debit transaction.
#
# Called by:
#   - Webhook handler (debiting Treasury on vendor settlement)
#   - Webhook handler (debiting Treasury on confirmed payout)
#   - orders/views.py (debiting vendor wallet on withdrawal request)
#
# Raises ValueError if balance is insufficient — callers must
# handle this exception and return an appropriate response.
# ============================================================
def debit_wallet(business, amount, user=None, description=''):
    """
    Debit a business wallet by the given amount.

    Args:
        business:    The Business instance whose wallet to debit
        amount:      Amount in Naira (Decimal or string)
        user:        The User associated with this transaction (optional)
        description: Human-readable description for the ledger entry

    Raises:
        ValueError: If the wallet has insufficient funds
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
                f'requested: ₦{amount_decimal} | available: ₦{wallet.balance}'
            )
            raise ValueError(
                f'Insufficient funds for business: {business.name}. '
                f'Available: ₦{wallet.balance}, Requested: ₦{amount_decimal}'
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
            f'amount: ₦{amount_decimal} | new balance: ₦{wallet.balance}'
        )