import json
import hmac
import hashlib
import logging
import traceback

from decimal import Decimal
from django.utils import timezone
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.contrib.auth import get_user_model

from core.models import PlatformConfig
from core.email_service import (
    send_transfer_initiated,
    send_transfer_completed,
    send_transfer_failed,
)
from transactions.models import Transaction, WebhookEvent
from transactions.services import (
    credit_wallet,
    debit_wallet,
    credit_escrow,
    get_commission_rate,
)
from tenants.models import Business, WithdrawalRequest
from orders.models import Order
from products.models import Product

logger = logging.getLogger('payfusion')
User = get_user_model()


def _get_treasury():
    system_user, _ = User.objects.get_or_create(
        email='system@payfusion.com',
        defaults={
            'username': 'system',
            'is_active': False,
            'is_staff': False,
        }
    )
    treasury, _ = Business.objects.get_or_create(
        slug='payfusion-system',
        defaults={
            'name': 'Payfusion Treasury',
            'owner': system_user,
        }
    )
    return treasury, system_user


@csrf_exempt
def paystack_webhook(request):

    payload = request.body
    paystack_signature = request.headers.get('x-paystack-signature')

    if not paystack_signature:
        logger.warning('Webhook received with no signature header — rejected')
        return HttpResponse(status=400)

    computed_signature = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode('utf-8'),
        payload,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(computed_signature, paystack_signature):
        logger.warning('Webhook signature mismatch — possible spoofed request')
        return HttpResponse(status=400)

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.error('Webhook payload is not valid JSON')
        return HttpResponse(status=400)

    event_type = data.get('event', '')
    event_data = data.get('data', {})
    reference = event_data.get('reference', '')
    event_id = str(data.get('id', '')) or None

    logger.info(f'Webhook received — event: {event_type} | reference: {reference}')

    if event_id:
        if WebhookEvent.objects.filter(event_id=event_id).exists():
            logger.info(f'Duplicate webhook event ignored — event_id: {event_id}')
            return HttpResponse(status=200)

    webhook_event = WebhookEvent.objects.create(
        event_id=event_id,
        event_type=event_type,
        reference=reference or None,
        payload=data,
        status='received',
    )

    try:
        if event_type == 'charge.success':
            _handle_charge_success(event_data, webhook_event)
        elif event_type == 'transfer.success':
            _handle_transfer_success(event_data, webhook_event)
        elif event_type == 'transfer.failed':
            _handle_transfer_failed(event_data, webhook_event)
        else:
            webhook_event.status = 'ignored'
            webhook_event.save(update_fields=['status'])
            logger.info(f'Unhandled webhook event type: {event_type}')

    except Exception as e:
        error_detail = traceback.format_exc()
        webhook_event.status = 'failed'
        webhook_event.error_message = error_detail
        webhook_event.save(update_fields=['status', 'error_message'])
        logger.error(f'Webhook handler crashed for {event_type} | {reference}: {e}')

    return HttpResponse(status=200)


# ============================================================
# HANDLER 1 — charge.success
#
# UPDATED FOR PHASE 9:
#   - Commission rate resolved per-order via get_commission_rate()
#     (vendor rate -> category rate -> platform default)
#   - If escrow_enabled: vendor settlement goes to escrow_balance
#     via credit_escrow() — held until delivery confirmed
#   - If escrow disabled: falls back to old behaviour —
#     direct credit to available balance via credit_wallet()
# ============================================================
def _handle_charge_success(event_data, webhook_event):

    reference = event_data.get('reference')

    try:
        txn = Transaction.objects.get(reference=reference)
    except Transaction.DoesNotExist:
        webhook_event.status = 'failed'
        webhook_event.error_message = f'Transaction not found: {reference}'
        webhook_event.save(update_fields=['status', 'error_message'])
        return

    if txn.status == 'paid':
        webhook_event.status = 'duplicate'
        webhook_event.save(update_fields=['status'])
        return

    amount_paid = Decimal(str(event_data['amount'])) / Decimal('100')

    if amount_paid != txn.amount:
        txn.status = 'failed'
        txn.save(update_fields=['status', 'updated_at'])
        webhook_event.status = 'failed'
        webhook_event.error_message = (
            f'Amount mismatch — expected: {txn.amount}, received: {amount_paid}'
        )
        webhook_event.save(update_fields=['status', 'error_message'])
        return

    config = PlatformConfig.get_config()
    treasury, system_user = _get_treasury()

    with transaction.atomic():

        txn = Transaction.objects.select_for_update().get(id=txn.id)

        if txn.status == 'paid':
            webhook_event.status = 'duplicate'
            webhook_event.save(update_fields=['status'])
            return

        txn.status = 'paid'
        txn.save(update_fields=['status', 'updated_at'])

        credit_wallet(
            business=treasury,
            amount=txn.amount,
            user=txn.user,
            description=f'Gross collection for checkout {txn.reference}'
        )

        orders = txn.orders.select_related('business').prefetch_related('items__product').all()

        for order in orders:

            if order.status == 'paid':
                continue

            # Resolve commission rate for this specific order.
            # Uses the first item's product/category as the
            # commission basis. An order with multiple products
            # from different categories uses the first item's
            # category — acceptable for now since most orders
            # are single-category. Multi-category commission
            # splitting can be added in a later phase if needed.
            first_item = order.items.first()
            product = first_item.product if first_item else None
            commission_rate = get_commission_rate(order.business, product)

            commission_fee = (order.total_amount * commission_rate).quantize(
                Decimal('0.01')
            )
            vendor_settlement = order.total_amount - commission_fee

            order.status = 'paid'
            order.commission_rate_applied = commission_rate
            order.commission_fee = commission_fee
            order.save(update_fields=['status', 'commission_rate_applied', 'commission_fee'])

            # Decrement stock for each item in this order.
            # Locked per-product to prevent overselling when
            # multiple simultaneous payments target the same product.
            for order_item in order.items.all():
                product = Product.objects.select_for_update().get(
                    id=order_item.product_id
                )

                new_stock = product.stock_quantity - order_item.quantity

                if new_stock < 0:
                    logger.warning(
                        f'Oversold detected — product: {product.name} | '
                        f'stock before: {product.stock_quantity} | '
                        f'requested: {order_item.quantity} | '
                        f'order: {order.reference}'
                    )
                    new_stock = 0

                product.stock_quantity = new_stock
                product.save(update_fields=['stock_quantity', 'updated_at'])

            # Debit Treasury for vendor's share regardless of
            # escrow setting — Treasury always allocates the
            # vendor settlement amount immediately.
            debit_wallet(
                business=treasury,
                amount=vendor_settlement,
                user=txn.user,
                description=f'Vendor payout allocation for order via {txn.reference}'
            )

            if config.escrow_enabled:
                # Hold in escrow until delivery confirmed
                credit_escrow(
                    business=order.business,
                    order=order,
                    txn=txn,
                    amount=vendor_settlement,
                    description=(
                        f'Escrow hold for order {order.reference} '
                        f'(commission: N{commission_fee} '
                        f'at {commission_rate * 100}%)'
                    ),
                )
                logger.info(
                    f'Vendor {order.business.name} escrow held N{vendor_settlement} '
                    f'(commission: N{commission_fee} at {commission_rate * 100}%) '
                    f'for order via {reference}'
                )
            else:
                # Escrow disabled — credit available balance directly
                credit_wallet(
                    business=order.business,
                    amount=vendor_settlement,
                    user=txn.user,
                    description=f'Settlement credit for order via {txn.reference}'
                )
                logger.info(
                    f'Vendor {order.business.name} credited N{vendor_settlement} '
                    f'(commission: N{commission_fee} at {commission_rate * 100}%) '
                    f'for order via {reference} [escrow disabled]'
                )

    webhook_event.status = 'processed'
    webhook_event.processed_at = timezone.now()
    webhook_event.save(update_fields=['status', 'processed_at'])

    logger.info(f'charge.success: Fully processed — reference: {reference}')


# ============================================================
# HANDLER 2 — transfer.success
# ============================================================
def _handle_transfer_success(event_data, webhook_event):

    reference = event_data.get('reference')

    try:
        txn = Transaction.objects.get(reference=reference)
    except Transaction.DoesNotExist:
        webhook_event.status = 'failed'
        webhook_event.error_message = f'Withdrawal transaction not found: {reference}'
        webhook_event.save(update_fields=['status', 'error_message'])
        return

    if txn.status in ['completed', 'failed']:
        webhook_event.status = 'duplicate'
        webhook_event.save(update_fields=['status'])
        return

    treasury, system_user = _get_treasury()

    with transaction.atomic():

        txn = Transaction.objects.select_for_update().get(id=txn.id)

        if txn.status == 'completed':
            webhook_event.status = 'duplicate'
            webhook_event.save(update_fields=['status'])
            return

        txn.status = 'completed'
        txn.save(update_fields=['status', 'updated_at'])

        debit_wallet(
            business=treasury,
            amount=txn.amount,
            user=system_user,
            description=f'Treasury finalised payout for withdrawal {txn.reference}'
        )

        try:
            wr = WithdrawalRequest.objects.get(transaction=txn)
            wr.status = 'completed'
            wr.completed_at = timezone.now()
            wr.save(update_fields=['status', 'completed_at', 'updated_at'])
            logger.info(f'WithdrawalRequest #{wr.id} marked completed — reference: {reference}')
            send_transfer_completed(wr.business, wr)
        except WithdrawalRequest.DoesNotExist:
            logger.info(f'No WithdrawalRequest found for transfer — reference: {reference}')

    webhook_event.status = 'processed'
    webhook_event.processed_at = timezone.now()
    webhook_event.save(update_fields=['status', 'processed_at'])

    logger.info(f'transfer.success: Payout confirmed — reference: {reference}')


# ============================================================
# HANDLER 3 — transfer.failed
# ============================================================
def _handle_transfer_failed(event_data, webhook_event):

    reference = event_data.get('reference')

    try:
        txn = Transaction.objects.get(reference=reference)
    except Transaction.DoesNotExist:
        webhook_event.status = 'failed'
        webhook_event.error_message = f'Withdrawal transaction not found: {reference}'
        webhook_event.save(update_fields=['status', 'error_message'])
        return

    if txn.status in ['completed', 'failed']:
        webhook_event.status = 'duplicate'
        webhook_event.save(update_fields=['status'])
        return

    with transaction.atomic():

        txn = Transaction.objects.select_for_update().get(id=txn.id)

        if txn.status == 'failed':
            webhook_event.status = 'duplicate'
            webhook_event.save(update_fields=['status'])
            return

        txn.status = 'failed'
        txn.save(update_fields=['status', 'updated_at'])

        credit_wallet(
            business=txn.business,
            amount=txn.amount,
            user=txn.user,
            description=f'Reversal: Failed transfer returned to wallet — {txn.reference}'
        )

        try:
            wr = WithdrawalRequest.objects.get(transaction=txn)
            wr.status = 'failed'
            wr.completed_at = timezone.now()
            wr.rejection_reason = 'Bank transfer failed. Funds have been returned to your wallet.'
            wr.save(update_fields=['status', 'completed_at', 'rejection_reason', 'updated_at'])
            logger.info(f'WithdrawalRequest #{wr.id} marked failed — reference: {reference}')
            send_transfer_failed(wr.business, wr)
        except WithdrawalRequest.DoesNotExist:
            logger.info(f'No WithdrawalRequest found for failed transfer — reference: {reference}')

    webhook_event.status = 'processed'
    webhook_event.processed_at = timezone.now()
    webhook_event.save(update_fields=['status', 'processed_at'])

    logger.info(f'transfer.failed: Funds reversed to vendor — reference: {reference}')