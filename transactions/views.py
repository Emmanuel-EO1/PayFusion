import json
import hmac
import hashlib
from decimal import Decimal

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.contrib.auth import get_user_model

from transactions.models import Transaction
from transactions.services import credit_wallet, debit_wallet
from tenants.models import Business, Wallet
from orders.models import Order

User = get_user_model()

@csrf_exempt
def paystack_webhook(request):
    payload = request.body
    paystack_signature = request.headers.get('x-paystack-signature')

    # Verify Webhook Authenticity
    computed_signature = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode(),
        payload,
        hashlib.sha512
    ).hexdigest()

    if computed_signature != paystack_signature:
        return HttpResponse(status=400)

    data = json.loads(payload)
    event = data.get('event')
    event_data = data.get('data', {})
    reference = event_data.get('reference')

    if not reference:
        return HttpResponse(status=200)

    # ==========================================
    # 1. HANDLE INFLOW PAYMENTS (CUSTOMER CHECKOUT)
    # ==========================================
    if event == 'charge.success':
        try:
            txn = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return HttpResponse(status=404)

        # Early Idempotency Protection Check
        if txn.status == 'paid':
            return HttpResponse(status=200)

        amount_paid = Decimal(event_data['amount']) / Decimal('100')
        if amount_paid != txn.amount:
            txn.status = 'failed'
            txn.save(update_fields=['status'])
            return HttpResponse(status=400)

        with transaction.atomic():
            # Row-level lock to prevent parallel processing race conditions
            txn = Transaction.objects.select_for_update().get(id=txn.id)
            if txn.status == 'paid':
                return HttpResponse(status=200)

            # Mark master transaction as paid immediately
            txn.status = 'paid'
            txn.transaction_type = 'payment'
            txn.save(update_fields=['status', 'transaction_type'])

            # Credit Platform Treasury with the FULL customer payment
            platform_business = txn.business  # Payfusion Treasury
            credit_wallet(
                business=platform_business,
                amount=txn.amount,
                user=txn.user,
                description=f"Gross collection for checkout {txn.reference}"
            )

            # Split and distribute funds to vendors
            orders = txn.orders.select_related('business').all()
            for order in orders:
                if order.status == 'paid':
                    continue

                order.status = 'paid'
                order.save(update_fields=['status'])

                # Set your commission percentage here (e.g., Decimal('0.05') for 5%)
                commission_rate = Decimal('0.10') 
                commission_fee = order.total_amount * commission_rate
                vendor_settlement_amount = order.total_amount - commission_fee

                # Deduct vendor's payout share from Treasury
                debit_wallet(
                    business=platform_business,
                    amount=vendor_settlement_amount,
                    user=txn.user,
                    description=f"Payout allocation for Order via checkout {txn.reference}"
                )

                # Credit the vendor's wallet with their settlement share
                credit_wallet(
                    business=order.business,
                    amount=vendor_settlement_amount,
                    user=txn.user,
                    description=f"Settlement value for Order via checkout {txn.reference}"
                )

        print(f"WEBHOOK SUCCESS: Processed payment inflow for reference: {reference}")
        return HttpResponse(status=200)

    # ==========================================
    # 2. HANDLE OUTFLOW PAYOUTS (VENDOR WITHDRAWALS)
    # ==========================================
    elif event in ['transfer.success', 'transfer.failed']:
        try:
            txn = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return HttpResponse(status=404)

        # Idempotency safety protection
        if txn.status in ['completed', 'failed']:
            return HttpResponse(status=200)

        # Safely locate or build system fallback entities
        system_user, _ = User.objects.get_or_create(
            email='system@payfusion.com', 
            defaults={'username': 'system', 'is_active': False, 'is_staff': False}
        )
        platform_treasury, _ = Business.objects.get_or_create(
            slug='payfusion-system',
            defaults={'name': 'Payfusion Treasury', 'owner': system_user}
        )

        # Transfer cleared successfully at the destination bank
        if event == 'transfer.success':
            with transaction.atomic():
                txn = Transaction.objects.select_for_update().get(id=txn.id)
                if txn.status == 'completed':
                    return HttpResponse(status=200)

                txn.status = 'completed'
                txn.save(update_fields=['status'])

                # Real cash has left the bank ecosystem. Debit treasury balance.
                debit_wallet(
                    business=platform_treasury,
                    amount=txn.amount,
                    user=system_user,
                    description=f"Treasury balance finalized payout for withdrawal {txn.reference}"
                )

        # Transfer failed/bounced at destination bank
        elif event == 'transfer.failed':
            with transaction.atomic():
                txn = Transaction.objects.select_for_update().get(id=txn.id)
                if txn.status == 'failed':
                    return HttpResponse(status=200)

                txn.status = 'failed'
                txn.save(update_fields=['status'])

                # Automated reversal: Return the held cash to the vendor's wallet balance
                credit_wallet(
                    business=txn.business,  # Vendor's business account
                    amount=txn.amount,
                    user=txn.user,
                    description=f"Reversal: Refund for failed external transfer reference {txn.reference}"
                )

        print(f"WEBHOOK SUCCESS: Processed payout status update for reference: {reference}")
        return HttpResponse(status=200)

    return HttpResponse(status=200)