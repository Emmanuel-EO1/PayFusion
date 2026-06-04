import logging

from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from cart.cart import Cart
from orders.models import Order, OrderItem
from tenants.models import Business, Wallet
from transactions.models import Transaction, generate_transaction_reference
from transactions.service.paystack import initialize_payment, verify_payment
from transactions.services import debit_wallet
from transactions.views import _get_treasury

logger = logging.getLogger('payfusion')


# ============================================================
# CHECKOUT
# Converts the cart into a master Transaction + per-vendor
# Orders + OrderItems, then redirects to payment summary.
#
# Flow:
#   1. Validate cart is not empty
#   2. Group cart items by vendor
#   3. Inside atomic block:
#      - Get/create Treasury business
#      - Create master Transaction (pending, assigned to Treasury)
#      - Create one Order per vendor
#      - Create OrderItems for each product
#      - Clear the cart
#   4. Redirect to payment summary
#
# Why atomic:
#   If any step fails (e.g. a product was deleted mid-checkout),
#   everything rolls back. No orphaned transactions or partial orders.
# ============================================================
@login_required
def checkout(request):
    cart = Cart(request)

    if len(cart) == 0:
        return redirect('cart:cart_detail')

    # ----------------------------------------------------------
    # Group cart items by vendor
    # We need one Order per vendor, so we organise first.
    # Using select_related on product.business prevents N+1 queries
    # ----------------------------------------------------------
    business_groups = {}

    for item in cart:
        product = item['product']

        if not product.business:
            logger.warning(
                f'Checkout blocked — product {product.id} has no business assigned'
            )
            return HttpResponseBadRequest('One or more products are invalid.')

        business = product.business

        if business.id not in business_groups:
            business_groups[business.id] = {
                'instance': business,
                'items': [],
                'total': Decimal('0.00'),
            }

        price = Decimal(str(product.price))
        quantity = item['quantity']

        business_groups[business.id]['items'].append({
            'product': product,
            'price': price,
            'quantity': quantity,
        })

        business_groups[business.id]['total'] += price * quantity

    # ----------------------------------------------------------
    # Build the total from groups (not cart.get_total_price())
    # This ensures our master transaction amount exactly matches
    # the sum of all vendor order totals — no rounding drift
    # ----------------------------------------------------------
    grand_total = sum(
        group['total'] for group in business_groups.values()
    ).quantize(Decimal('0.01'))

    try:
        with transaction.atomic():

            # Get the PayFusion Treasury business
            # Master transaction is assigned to Treasury because
            # a single transaction cannot belong to multiple vendors
            treasury, system_user = _get_treasury()

            # Create the master transaction
            # This is what Paystack will reference during payment
            master_txn = Transaction.objects.create(
                user=request.user,
                business=treasury,
                amount=grand_total,
                status='pending',
                transaction_type='payment',
                reference=generate_transaction_reference(),
            )

            # Create one Order per vendor
            for group in business_groups.values():

                order = Order.objects.create(
                    user=request.user,
                    business=group['instance'],
                    transaction=master_txn,
                    status='pending',
                    total_amount=group['total'],
                )

                # Create one OrderItem per product in this vendor's group
                for item in group['items']:
                    OrderItem.objects.create(
                        order=order,
                        product=item['product'],
                        price=item['price'],
                        quantity=item['quantity'],
                    )

            # Clear the cart INSIDE the atomic block
            # If anything above failed, this won't execute either
            # preventing the cart from clearing on a failed checkout
            cart.clear()

    except Exception as e:
        logger.error(f'Checkout failed for user {request.user.id}: {e}')
        return render(request, 'orders/checkout_error.html', {
            'message': 'Something went wrong during checkout. Please try again.'
        })

    logger.info(
        f'Checkout successful — user: {request.user.id} | '
        f'reference: {master_txn.reference} | amount: ₦{grand_total}'
    )

    return redirect('orders:payment_summary', transaction_id=master_txn.id)


# ============================================================
# PAYMENT SUMMARY
# Shows the customer a breakdown of their order before paying.
# Displays each vendor's order and the total amount.
# ============================================================
@login_required
def payment_summary(request, transaction_id):
    txn = get_object_or_404(
        Transaction,
        id=transaction_id,
        user=request.user,
    )

    orders = txn.orders.select_related('business').prefetch_related('items__product').all()

    return render(request, 'orders/payment_summary.html', {
        'transaction': txn,
        'orders': orders,
    })


# ============================================================
# INITIALIZE PAYSTACK PAYMENT
# Calls Paystack API to create a payment session and
# redirects the customer to Paystack's hosted checkout page.
# ============================================================
@login_required
def initialize_paystack_payment(request, transaction_id):

    txn = get_object_or_404(
        Transaction,
        id=transaction_id,
        user=request.user,
    )

    # Already paid — redirect to success page directly
    if txn.status == 'paid':
        return redirect('orders:payment_success', reference=txn.reference)

    # Don't allow initializing a failed transaction
    if txn.status == 'failed':
        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'This transaction has already failed. Please start a new order.',
        })

    response = initialize_payment(
        email=request.user.email,
        amount=txn.amount,
        reference=txn.reference,
    )

    if response.get('status') is True:
        payment_url = response['data']['authorization_url']
        logger.info(
            f'Payment initialized — user: {request.user.id} | reference: {txn.reference}'
        )
        return redirect(payment_url)

    # Paystack returned an error or was unreachable
    logger.error(
        f'Payment initialization failed — reference: {txn.reference} | '
        f'response: {response.get("message")}'
    )
    return render(request, 'orders/payment_failed.html', {
        'txn': txn,
        'message': 'We could not connect to the payment gateway. Please try again.',
        'show_retry': True,
    })


# ============================================================
# PAYMENT CALLBACK
# Paystack redirects the customer back to this URL after payment.
# This is the BROWSER path — the webhook is the SERVER path.
#
# Why no @login_required:
#   Paystack's redirect does not preserve the user's browser session
#   in all cases — especially on mobile browsers or when the payment
#   page opens in a new tab. Forcing login here would send the user
#   to the login page AFTER successfully paying, which is terrible UX.
#
# Security is maintained by:
#   1. The reference is a cryptographically random UUID — unguessable
#   2. We only display information — no financial processing happens here
#   3. All actual money movement happens in the webhook handler
#   4. Even if someone guesses a reference, they only see a status page
#
# This view's only job is to show the correct status page.
# The webhook has already (or will shortly) process the payment.
# ============================================================
def payment_callback(request):

    reference = request.GET.get('reference')

    # No reference — user cancelled or closed the payment page
    if not reference:
        return render(request, 'orders/payment_failed.html', {
            'message': 'Payment was cancelled or interrupted. No charge was made.',
        })

    # Fetch transaction by reference — 404 if not found
    txn = get_object_or_404(Transaction, reference=reference)

    # ----------------------------------------------------------
    # Webhook already processed this payment
    # This is the ideal case — webhook arrived before the redirect
    # Show success immediately
    # ----------------------------------------------------------
    if txn.status == 'paid':
        orders = txn.orders.select_related('business').all()
        return render(request, 'orders/payment_success.html', {
            'txn': txn,
            'orders': orders,
        })

    # Already marked as failed
    if txn.status == 'failed':
        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'This payment was unsuccessful. No charge was made.',
        })

    # ----------------------------------------------------------
    # Webhook hasn't arrived yet — verify directly with Paystack
    # This handles the case where the redirect is faster than
    # the webhook (common on fast connections)
    # ----------------------------------------------------------
    response = verify_payment(reference)

    # Paystack API was unreachable — show processing page
    # Webhook will eventually arrive and settle this
    if not response.get('status'):
        logger.warning(
            f'Payment callback: Could not verify with Paystack — reference: {reference}'
        )
        return render(request, 'orders/payment_processing.html', {
            'txn': txn,
            'message': 'Your payment is being confirmed. This page will update shortly.',
        })

    data = response.get('data', {})
    paystack_status = data.get('status')

    # ----------------------------------------------------------
    # Paystack confirmed payment failed
    # Mark as failed here as a fallback — webhook will also fire
    # but we don't want the user waiting on a processing screen
    # for a payment Paystack already told us failed
    # ----------------------------------------------------------
    if paystack_status != 'success':
        txn.status = 'failed'
        txn.save(update_fields=['status', 'updated_at'])

        logger.info(
            f'Payment callback: Failed payment — '
            f'reference: {reference} | paystack_status: {paystack_status}'
        )
        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'Your payment was not successful. No charge was made.',
        })

    # ----------------------------------------------------------
    # Amount validation
    # Even on the callback path we validate the amount
    # Paystack sends Kobo — convert to Naira for comparison
    # ----------------------------------------------------------
    amount_paid = Decimal(str(data.get('amount', 0))) / Decimal('100')

    if amount_paid != txn.amount:
        txn.status = 'failed'
        txn.save(update_fields=['status', 'updated_at'])

        logger.error(
            f'Payment callback: Amount mismatch — '
            f'expected ₦{txn.amount}, received ₦{amount_paid} | reference: {reference}'
        )
        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'A payment verification error occurred. Please contact support.',
        })

    # ----------------------------------------------------------
    # Payment verified as successful on callback
    # But we do NOT process the wallet credits here.
    # The webhook is the authoritative source for financial processing.
    # We show the processing screen and let the webhook settle it.
    #
    # Why not process here too:
    #   If both callback AND webhook process the payment,
    #   the vendor gets credited twice. The webhook's idempotency
    #   guard would catch it, but the correct design is to have
    #   one authoritative processing path — the webhook.
    # ----------------------------------------------------------
    logger.info(
        f'Payment callback: Verified success, awaiting webhook — reference: {reference}'
    )
    return render(request, 'orders/payment_processing.html', {
        'txn': txn,
        'message': 'Payment confirmed! Finalising your order...',
    })


# ============================================================
# PAYMENT SUCCESS
# Direct success page accessible by reference.
# Shown after webhook confirms payment, or when user
# returns to a previously paid transaction.
# ============================================================
def payment_success(request, reference):
    txn = get_object_or_404(Transaction, reference=reference)

    if txn.status != 'paid':
        return redirect('orders:payment_callback')

    orders = txn.orders.select_related('business').prefetch_related('items__product').all()

    return render(request, 'orders/payment_success.html', {
        'txn': txn,
        'orders': orders,
    })


# ============================================================
# WITHDRAWAL PAGE
# Shows the vendor their wallet balance and withdrawal form.
# Displays available balance per business they own.
# ============================================================
@login_required
def withdrawal_page(request):
    businesses = Business.objects.filter(
        owner=request.user
    ).select_related('wallet')

    return render(request, 'orders/withdrawal.html', {
        'businesses': businesses,
    })


# ============================================================
# REQUEST WITHDRAWAL
# Handles a vendor's withdrawal request.
#
# Current flow:
#   1. Validate ownership and amount
#   2. Check sufficient balance
#   3. Debit wallet immediately as a hold
#   4. Create withdrawal Transaction (pending)
#   5. Return confirmation page
#
# NOT YET wired to Paystack Transfer API — that comes in the
# full WithdrawalRequest architecture (next phase) which adds:
#   - Hold period gate
#   - Automated audit checks
#   - BankAccount model
#   - Admin approval for large amounts
#   - Actual Paystack transfer call
# ============================================================
@require_POST
@login_required
def request_withdrawal(request):

    business_id = request.POST.get('business_id')

    # Verify the business belongs to the logged-in user
    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    # Safely parse and validate the requested amount
    try:
        amount_to_withdraw = Decimal(str(request.POST.get('amount', '0')))
    except (TypeError, ValueError, InvalidOperation):
        return render(request, 'orders/withdrawal.html', {
            'businesses': Business.objects.filter(
                owner=request.user
            ).select_related('wallet'),
            'error': 'Invalid amount. Please enter a valid number.',
        })

    if amount_to_withdraw <= 0:
        return render(request, 'orders/withdrawal.html', {
            'businesses': Business.objects.filter(
                owner=request.user
            ).select_related('wallet'),
            'error': 'Withdrawal amount must be greater than zero.',
        })

    # Minimum withdrawal amount — prevents micro-withdrawal spam
    MINIMUM_WITHDRAWAL = Decimal('500.00')
    if amount_to_withdraw < MINIMUM_WITHDRAWAL:
        return render(request, 'orders/withdrawal.html', {
            'businesses': Business.objects.filter(
                owner=request.user
            ).select_related('wallet'),
            'error': f'Minimum withdrawal amount is ₦{MINIMUM_WITHDRAWAL}.',
        })

    try:
        with transaction.atomic():

            # Lock the wallet row to prevent race conditions
            # If two withdrawal requests arrive simultaneously,
            # only one can proceed at a time
            wallet = Wallet.objects.select_for_update().get(business=business)

            if wallet.balance < amount_to_withdraw:
                return render(request, 'orders/withdrawal.html', {
                    'businesses': Business.objects.filter(
                        owner=request.user
                    ).select_related('wallet'),
                    'error': (
                        f'Insufficient funds. Your available balance is '
                        f'₦{wallet.balance}.'
                    ),
                })

            # Debit the wallet immediately as a hold
            # This prevents the vendor from spending the same funds
            # while the withdrawal is being processed
            debit_wallet(
                business=business,
                amount=amount_to_withdraw,
                user=request.user,
                description=(
                    f'Withdrawal hold — ₦{amount_to_withdraw} pending transfer '
                    f'to external bank account.'
                ),
            )

            # Create the withdrawal transaction record
            withdrawal_txn = Transaction.objects.create(
                user=request.user,
                business=business,
                amount=amount_to_withdraw,
                transaction_type='withdrawal',
                status='pending',
                description='Vendor withdrawal — pending bank transfer',
            )

            logger.info(
                f'Withdrawal initiated — vendor: {business.name} | '
                f'amount: ₦{amount_to_withdraw} | reference: {withdrawal_txn.reference}'
            )

        # -------------------------------------------------------
        # PAYSTACK TRANSFER CALL GOES HERE (next phase)
        # After the WithdrawalRequest architecture is built,
        # this is where the hold period check and audit gates
        # will sit before calling:
        # initiate_transfer(amount, recipient_code, reference)
        # -------------------------------------------------------

        return render(request, 'orders/withdrawal_initiated.html', {
            'txn': withdrawal_txn,
            'business': business,
        })

    except Wallet.DoesNotExist:
        logger.error(
            f'Withdrawal failed — wallet not found for business: {business.id}'
        )
        return render(request, 'orders/withdrawal.html', {
            'businesses': Business.objects.filter(
                owner=request.user
            ).select_related('wallet'),
            'error': 'Wallet not found. Please contact support.',
        })

    except Exception as e:
        logger.error(
            f'Withdrawal failed — business: {business.id} | '
            f'amount: {amount_to_withdraw} | error: {e}'
        )
        return render(request, 'orders/withdrawal.html', {
            'businesses': Business.objects.filter(
                owner=request.user
            ).select_related('wallet'),
            'error': 'Something went wrong. Please try again.',
        })