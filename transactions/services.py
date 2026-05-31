from django.db import transaction
from decimal import Decimal
from tenants.models import Wallet
from transactions.models import Transaction
import uuid


def generate_reference():
    return uuid.uuid4().hex[:12].upper()


def credit_wallet(business, amount, user=None, description=""):
    # Ensure atomic execution so row locking works
    with transaction.atomic():
        # 1. Lock the wallet row in the database completely
        wallet, _ = Wallet.objects.select_for_update().get_or_create(
            business=business,
            defaults={'balance': Decimal('0.00')}
        )

        # 2. Direct Python math (NO F expressions). This updates the raw number instantly.
        wallet.balance = wallet.balance + Decimal(str(amount))
        wallet.save(update_fields=['balance'])

        # 3. Log the completed transaction ledger entry
        Transaction.objects.create(
            user=user,
            business=business,
            amount=amount,
            transaction_type='credit',
            status='completed',
            reference=generate_reference(),
            description=description
        )


def debit_wallet(business, amount, user=None, description=""):
    with transaction.atomic():
        # 1. Lock the wallet row to read the absolute latest true balance
        wallet, _ = Wallet.objects.select_for_update().get_or_create(
            business=business,
            defaults={'balance': Decimal('0.00')}
        )

        # 2. Strict, accurate numeric evaluation
        amount_decimal = Decimal(str(amount))
        if wallet.balance < amount_decimal:
            raise ValueError(f"Insufficient funds for business: {business.name}")

        # 3. Direct Python math deduction
        wallet.balance = wallet.balance - amount_decimal
        wallet.save(update_fields=['balance'])

        # 4. Log the completed transaction ledger entry
        Transaction.objects.create(
            user=user,
            business=business,
            amount=amount,
            transaction_type='debit',
            status='completed',
            reference=generate_reference(),
            description=description
        )