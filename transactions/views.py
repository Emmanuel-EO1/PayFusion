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

from transactions.models import Transaction, WebhookEvent
from transactions.services import credit_wallet, debit_wallet
from tenants.models import Business
from orders.models import Order

logger = logging.getLogger('payfusion')
User = get_user_model()


# ============================================================
# HELPER — Get or create the PayFusion Treasury business
# This is called in multiple places so we centralise it here
# ============================================================
def _get_treasury():
    """
    Safely fetch the PayFusion Treasury business and its system owner.
    Uses get_or_create so it never crashes even if somehow missing.
    The Treasury is the central escrow account that receives all
    customer payments before distributing to vendors.
    """
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


# ============================================================
# MAIN WEBHOOK ENDPOINT
# URL: /transactions/payment/webhook/
# Method: POST
# Called by: Paystack server-to-server (not the browser)
# ============================================================
@csrf_exempt
def paystack_webhook(request):
    """
    Central entry point for all Paystack webhook events.

    Security model:
    - CSRF exempt because this is server-to-server (no browser session)
    - Protected instead by HMAC-SHA512 signature verification
    - Every event is logged to WebhookEvent before any processing
    - Always returns 200 after logging to prevent Paystack retries
      that could cause double processing
    """

    # ----------------------------------------------------------
    # STEP 1: Read the raw request body
    # We must read the RAW bytes before any parsing.
    # If we parse first and reconstruct, the signature will not match
    # because even a single whitespace difference breaks HMAC.
    # ----------------------------------------------------------
    payload = request.body
    paystack_signature = request.headers.get('x-paystack-signature')

    # ----------------------------------------------------------
    # STEP 2: Verify the request genuinely came from Paystack
    # Paystack signs every webhook with HMAC-SHA512 using your
    # secret key. We recompute the signature and compare.
    # If they don't match, someone is sending fake webhooks.
    # ----------------------------------------------------------
    if not paystack_signature:
        logger.warning('Webhook received with no signature header — rejected')
        return HttpResponse(status=400)

    computed_signature = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode('utf-8'),
        payload,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(computed_signature, paystack_signature):
        # compare_digest prevents timing attacks —
        # a character-by-character comparison would leak information
        # about how many characters matched before the mismatch
        logger.warning('Webhook signature mismatch — possible spoofed request')
        return HttpResponse(status=400)

    # ----------------------------------------------------------
    # STEP 3: Parse the JSON payload
    # ----------------------------------------------------------
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.error('Webhook payload is not valid JSON')
        return HttpResponse(status=400)

    event_type = data.get('event', '')
    event_data = data.get('data', {})
    reference = event_data.get('reference', '')

    # Paystack includes a unique ID on every event
    # We use this to detect and block duplicate deliveries
    event_id = str(data.get('id', '')) or None

    logger.info(f'Webhook received — event: {event_type} | reference: {reference}')

    # ----------------------------------------------------------
    # STEP 4: Duplicate event detection
    # Paystack retries events if they don't receive a fast 200.
    # We check if we've already seen this exact event_id.
    # If yes, we log it as duplicate and return 200 immediately
    # — no processing, no double crediting.
    # ----------------------------------------------------------
    if event_id:
        if WebhookEvent.objects.filter(event_id=event_id).exists():
            logger.info(f'Duplicate webhook event ignored — event_id: {event_id}')
            return HttpResponse(status=200)

    # ----------------------------------------------------------
    # STEP 5: Log the raw event to the database IMMEDIATELY
    # We save the complete payload before processing anything.
    # This means even if our handler crashes on the next line,
    # we have permanent proof the event arrived and what it contained.
    # ----------------------------------------------------------
    webhook_event = WebhookEvent.objects.create(
        event_id=event_id,
        event_type=event_type,
        reference=reference or None,
        payload=data,
        status='received',
    )

    # ----------------------------------------------------------
    # STEP 6: Route to the correct handler
    # We wrap the entire processing in a try/except so that
    # any crash is caught, logged to the WebhookEvent record,
    # and we still return 200 to prevent Paystack retries.
    # ----------------------------------------------------------
    try:

        if event_type == 'charge.success':
            _handle_charge_success(event_data, webhook_event)

        elif event_type == 'transfer.success':
            _handle_transfer_success(event_data, webhook_event)

        elif event_type == 'transfer.failed':
            _handle_transfer_failed(event_data, webhook_event)

        else:
            # Unrecognised event type — log and move on
            # We do not return 400 here because this is not an error,
            # Paystack simply sends events we haven't implemented yet
            webhook_event.status = 'ignored'
            webhook_event.save(update_fields=['status'])
            logger.info(f'Unhandled webhook event type: {event_type}')

    except Exception as e:
        # Something crashed inside a handler.
        # Save the full traceback to the WebhookEvent record
        # so we can debug it from the admin panel.
        error_detail = traceback.format_exc()
        webhook_event.status = 'failed'
        webhook_event.error_message = error_detail
        webhook_event.save(update_fields=['status', 'error_message'])
        logger.error(f'Webhook handler crashed for {event_type} | {reference}: {e}')

    # Always return 200 — Paystack expects this regardless of outcome
    return HttpResponse(status=200)


# ============================================================
# HANDLER 1 — charge.success
# Triggered when a customer's payment is confirmed by Paystack.
# Responsibilities:
#   - Credit the Treasury with the full payment amount
#   - Split and distribute funds to each vendor
#   - Apply platform commission (currently 10%)
#   - Mark all related orders as paid
# ============================================================
def _handle_charge_success(event_data, webhook_event):
    """
    Processes a confirmed customer payment.
    Uses two-layer idempotency + row-level locking for safety.
    """
    reference = event_data.get('reference')

    # Fetch the master transaction — created during checkout
    try:
        txn = Transaction.objects.get(reference=reference)
    except Transaction.DoesNotExist:
        webhook_event.status = 'failed'
        webhook_event.error_message = f'Transaction not found for reference: {reference}'
        webhook_event.save(update_fields=['status', 'error_message'])
        logger.error(f'charge.success: Transaction not found — reference: {reference}')
        return

    # ----------------------------------------------------------
    # IDEMPOTENCY LAYER 1 — fast check before acquiring any locks
    # If already paid, this event was already processed successfully.
    # Log as duplicate and exit cleanly.
    # ----------------------------------------------------------
    if txn.status == 'paid':
        webhook_event.status = 'duplicate'
        webhook_event.save(update_fields=['status'])
        logger.info(f'charge.success: Already processed — reference: {reference}')
        return

    # ----------------------------------------------------------
    # AMOUNT VALIDATION
    # Paystack sends amounts in Kobo (smallest currency unit).
    # ₦1,000 = 100,000 Kobo. We divide by 100 to get Naira.
    # If the amount doesn't match what we expect, something is wrong
    # — could be a tampered request or a Paystack edge case.
    # ----------------------------------------------------------
    amount_paid = Decimal(str(event_data['amount'])) / Decimal('100')

    if amount_paid != txn.amount:
        txn.status = 'failed'
        txn.save(update_fields=['status', 'updated_at'])
        webhook_event.status = 'failed'
        webhook_event.error_message = (
            f'Amount mismatch — expected: {txn.amount}, received: {amount_paid}'
        )
        webhook_event.save(update_fields=['status', 'error_message'])
        logger.error(
            f'charge.success: Amount mismatch — '
            f'expected {txn.amount}, got {amount_paid} | reference: {reference}'
        )
        return

    treasury, system_user = _get_treasury()

    # ----------------------------------------------------------
    # ATOMIC BLOCK + ROW LOCK
    # Everything from here runs inside a single database transaction.
    # If anything fails, ALL changes are rolled back — no partial states.
    #
    # select_for_update() locks the transaction row so that if two
    # webhook events arrive simultaneously (Paystack retry scenario),
    # only one can proceed at a time. The second one hits idempotency
    # layer 2 below after acquiring the lock.
    # ----------------------------------------------------------
    with transaction.atomic():

        # Re-fetch with lock
        txn = Transaction.objects.select_for_update().get(id=txn.id)

        # ----------------------------------------------------------
        # IDEMPOTENCY LAYER 2 — inside the lock
        # Catches the race condition where two simultaneous webhook
        # calls both passed layer 1 before either acquired the lock.
        # ----------------------------------------------------------
        if txn.status == 'paid':
            webhook_event.status = 'duplicate'
            webhook_event.save(update_fields=['status'])
            return

        # Mark the master transaction as paid
        txn.status = 'paid'
        txn.save(update_fields=['status', 'updated_at'])

        # Credit the full payment amount to the Treasury
        # The Treasury acts as escrow — it holds all funds
        # before distributing vendor shares
        credit_wallet(
            business=treasury,
            amount=txn.amount,
            user=txn.user,
            description=f'Gross collection for checkout {txn.reference}'
        )

        # Fetch all vendor orders linked to this master transaction
        orders = txn.orders.select_related('business').all()

        for order in orders:

            # Skip orders already processed (safety against partial reruns)
            if order.status == 'paid':
                continue

            # --------------------------------------------------
            # COMMISSION CALCULATION
            # Platform takes 10% of each vendor's order total.
            # Vendor receives 90%.
            #
            # Example:
            #   Order total      = ₦10,000
            #   Commission (10%) = ₦1,000
            #   Vendor receives  = ₦9,000
            #   Treasury keeps   = ₦1,000 (already received full ₦10,000)
            # --------------------------------------------------
            commission_rate = Decimal('0.10')
            commission_fee = (order.total_amount * commission_rate).quantize(Decimal('0.01'))
            vendor_settlement = order.total_amount - commission_fee

            # Mark this vendor's order as paid
            order.status = 'paid'
            order.save(update_fields=['status'])

            # Debit the vendor's settlement share from Treasury
            # (Treasury received the full amount, now pays out vendor share)
            debit_wallet(
                business=treasury,
                amount=vendor_settlement,
                user=txn.user,
                description=f'Vendor payout allocation for order via {txn.reference}'
            )

            # Credit vendor's wallet with their settlement amount
            credit_wallet(
                business=order.business,
                amount=vendor_settlement,
                user=txn.user,
                description=f'Settlement credit for order via {txn.reference}'
            )

            logger.info(
                f'Vendor {order.business.name} credited ₦{vendor_settlement} '
                f'(commission: ₦{commission_fee}) for order via {reference}'
            )

    # Mark the webhook event as successfully processed
    webhook_event.status = 'processed'
    webhook_event.processed_at = timezone.now()
    webhook_event.save(update_fields=['status', 'processed_at'])

    logger.info(f'charge.success: Fully processed — reference: {reference}')


# ============================================================
# HANDLER 2 — transfer.success
# Triggered when a vendor payout reaches their bank successfully.
# At this point, real cash has physically left the platform.
# Responsibilities:
#   - Mark the withdrawal transaction as completed
#   - Debit the Treasury (confirming cash has left the system)
# Note: The vendor wallet was already debited at withdrawal request
# time (in orders/views.py) as a hold. This handler just finalises
# the Treasury side of that movement.
# ============================================================
def _handle_transfer_success(event_data, webhook_event):
    """
    Finalises a vendor payout that successfully reached the bank.
    """
    reference = event_data.get('reference')

    try:
        txn = Transaction.objects.get(reference=reference)
    except Transaction.DoesNotExist:
        webhook_event.status = 'failed'
        webhook_event.error_message = f'Withdrawal transaction not found: {reference}'
        webhook_event.save(update_fields=['status', 'error_message'])
        return

    # Idempotency — already finalised
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

        # Real cash has left the bank ecosystem
        # Debit Treasury to reflect actual outflow
        debit_wallet(
            business=treasury,
            amount=txn.amount,
            user=system_user,
            description=f'Treasury finalised payout for withdrawal {txn.reference}'
        )

    webhook_event.status = 'processed'
    webhook_event.processed_at = timezone.now()
    webhook_event.save(update_fields=['status', 'processed_at'])

    logger.info(f'transfer.success: Payout confirmed — reference: {reference}')


# ============================================================
# HANDLER 3 — transfer.failed
# Triggered when a vendor payout bounces at the destination bank.
# The money never actually left the platform.
# Responsibilities:
#   - Mark the withdrawal transaction as failed
#   - Reverse the vendor wallet debit (return funds to vendor)
#   - Treasury is NOT touched (money never physically left)
# ============================================================
def _handle_transfer_failed(event_data, webhook_event):
    """
    Reverses a vendor payout that failed at the destination bank.
    Automatically returns funds to the vendor's wallet.
    """
    reference = event_data.get('reference')

    try:
        txn = Transaction.objects.get(reference=reference)
    except Transaction.DoesNotExist:
        webhook_event.status = 'failed'
        webhook_event.error_message = f'Withdrawal transaction not found: {reference}'
        webhook_event.save(update_fields=['status', 'error_message'])
        return

    # Idempotency — already finalised
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

        # Automatic reversal — return the held funds to vendor's wallet
        # The vendor wallet was debited when they requested withdrawal.
        # Since the transfer failed, the money never left — give it back.
        credit_wallet(
            business=txn.business,
            amount=txn.amount,
            user=txn.user,
            description=f'Reversal: Failed transfer returned to wallet — {txn.reference}'
        )

    webhook_event.status = 'processed'
    webhook_event.processed_at = timezone.now()
    webhook_event.save(update_fields=['status', 'processed_at'])

    logger.info(f'transfer.failed: Funds reversed to vendor — reference: {reference}')