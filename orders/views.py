import logging

from decimal import Decimal, InvalidOperation
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from cart.cart import Cart
from core.models import PlatformConfig
from orders.models import Order, OrderItem
from tenants.models import Business, Wallet, BankAccount, WithdrawalRequest
from transactions.models import Transaction, generate_transaction_reference
from transactions.service.paystack import (
    initialize_payment,
    verify_payment,
    get_banks,
    resolve_bank_account,
    create_transfer_recipient,
    initiate_transfer,
)
from transactions.services import debit_wallet
from transactions.views import _get_treasury

logger = logging.getLogger('payfusion')


# ============================================================
# CHECKOUT
# ============================================================
@login_required
def checkout(request):
    cart = Cart(request)

    if len(cart) == 0:
        return redirect('cart:cart_detail')

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

    grand_total = sum(
        group['total'] for group in business_groups.values()
    ).quantize(Decimal('0.01'))

    try:
        with transaction.atomic():

            treasury, system_user = _get_treasury()

            master_txn = Transaction.objects.create(
                user=request.user,
                business=treasury,
                amount=grand_total,
                status='pending',
                transaction_type='payment',
                reference=generate_transaction_reference(),
            )

            for group in business_groups.values():

                order = Order.objects.create(
                    user=request.user,
                    business=group['instance'],
                    transaction=master_txn,
                    status='pending',
                    total_amount=group['total'],
                )

                for item in group['items']:
                    OrderItem.objects.create(
                        order=order,
                        product=item['product'],
                        price=item['price'],
                        quantity=item['quantity'],
                    )

            cart.clear()

    except Exception as e:
        logger.error(f'Checkout failed for user {request.user.id}: {e}')
        return render(request, 'orders/checkout_error.html', {
            'message': 'Something went wrong during checkout. Please try again.'
        })

    logger.info(
        f'Checkout successful — user: {request.user.id} | '
        f'reference: {master_txn.reference} | amount: N{grand_total}'
    )

    return redirect('orders:payment_summary', transaction_id=master_txn.id)


# ============================================================
# PAYMENT SUMMARY
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
# ============================================================
@login_required
def initialize_paystack_payment(request, transaction_id):

    txn = get_object_or_404(
        Transaction,
        id=transaction_id,
        user=request.user,
    )

    if txn.status == 'paid':
        return redirect('orders:payment_success', reference=txn.reference)

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
# ============================================================
def payment_callback(request):

    reference = request.GET.get('reference')

    if not reference:
        return render(request, 'orders/payment_failed.html', {
            'message': 'Payment was cancelled or interrupted. No charge was made.',
        })

    txn = get_object_or_404(Transaction, reference=reference)

    if txn.status == 'paid':
        orders = txn.orders.select_related('business').all()
        return render(request, 'orders/payment_success.html', {
            'txn': txn,
            'orders': orders,
        })

    if txn.status == 'failed':
        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'This payment was unsuccessful. No charge was made.',
        })

    response = verify_payment(reference)

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

    if paystack_status != 'success':
        txn.status = 'failed'
        txn.save(update_fields=['status', 'updated_at'])
        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'Your payment was not successful. No charge was made.',
        })

    amount_paid = Decimal(str(data.get('amount', 0))) / Decimal('100')

    if amount_paid != txn.amount:
        txn.status = 'failed'
        txn.save(update_fields=['status', 'updated_at'])
        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'A payment verification error occurred. Please contact support.',
        })

    return render(request, 'orders/payment_processing.html', {
        'txn': txn,
        'message': 'Payment confirmed! Finalising your order...',
    })


# ============================================================
# PAYMENT SUCCESS
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
# BANK ACCOUNTS — LIST
# Shows vendor all their saved bank accounts
# ============================================================
@login_required
def bank_accounts(request):
    """
    List all active bank accounts for all businesses
    owned by the logged-in vendor.
    """
    businesses = Business.objects.filter(
        owner=request.user
    ).prefetch_related('bank_accounts')

    return render(request, 'orders/bank_accounts.html', {
        'businesses': businesses,
    })


# ============================================================
# BANK ACCOUNTS — ADD
#
# Two-step process:
#   Step 1 (GET)  → Show form with bank dropdown + account number field
#   Step 2 (POST) → Resolve account → create recipient → save
#
# Paystack calls made:
#   1. resolve_bank_account() — verify account exists, get account name
#   2. create_transfer_recipient() — register with Paystack, get recipient_code
# ============================================================
@login_required
def add_bank_account(request):
    """
    Add and verify a new bank account for withdrawals.
    Calls Paystack to verify the account exists and
    registers it as a transfer recipient.
    """
    # Get businesses owned by this vendor for the form
    businesses = Business.objects.filter(owner=request.user)

    if not businesses.exists():
        return render(request, 'orders/bank_accounts.html', {
            'error': 'You must register a business before adding a bank account.',
        })

    if request.method == 'GET':
        # Fetch bank list from Paystack for the dropdown
        banks_response = get_banks()

        if not banks_response.get('status'):
            return render(request, 'orders/add_bank_account.html', {
                'businesses': businesses,
                'error': 'Could not load bank list. Please try again.',
            })

        return render(request, 'orders/add_bank_account.html', {
            'businesses': businesses,
            'banks': banks_response.get('data', []),
        })

    # POST — process the form submission
    business_id    = request.POST.get('business_id')
    account_number = request.POST.get('account_number', '').strip()
    bank_code      = request.POST.get('bank_code', '').strip()
    bank_name      = request.POST.get('bank_name', '').strip()
    set_as_primary = request.POST.get('set_as_primary') == 'on'

    # Verify business belongs to this vendor
    business = get_object_or_404(Business, id=business_id, owner=request.user)

    # Basic validation
    if not account_number or len(account_number) != 10 or not account_number.isdigit():
        banks_response = get_banks()
        return render(request, 'orders/add_bank_account.html', {
            'businesses': businesses,
            'banks': banks_response.get('data', []),
            'error': 'Please enter a valid 10-digit account number.',
        })

    # ----------------------------------------------------------
    # STEP 1 — Resolve the bank account with Paystack
    # Confirms account exists and returns account holder name
    # ----------------------------------------------------------
    resolve_response = resolve_bank_account(account_number, bank_code)

    if not resolve_response.get('status'):
        banks_response = get_banks()
        return render(request, 'orders/add_bank_account.html', {
            'businesses': businesses,
            'banks': banks_response.get('data', []),
            'error': (
                'Could not verify this account number. '
                'Please check your details and try again.'
            ),
        })

    account_name = resolve_response['data']['account_name']

    # ----------------------------------------------------------
    # STEP 2 — Register as Paystack Transfer Recipient
    # Returns recipient_code used for all future transfers
    # ----------------------------------------------------------
    recipient_response = create_transfer_recipient(
        account_name=account_name,
        account_number=account_number,
        bank_code=bank_code,
    )

    if not recipient_response.get('status'):
        banks_response = get_banks()
        return render(request, 'orders/add_bank_account.html', {
            'businesses': businesses,
            'banks': banks_response.get('data', []),
            'error': (
                'Could not register this bank account for transfers. '
                'Please try again or contact support.'
            ),
        })

    recipient_code = recipient_response['data']['recipient_code']

    # ----------------------------------------------------------
    # STEP 3 — Save to database
    # is_verified=True because Paystack confirmed it
    # recipient_code saved for all future transfers
    # ----------------------------------------------------------
    bank_account = BankAccount.objects.create(
        business=business,
        account_name=account_name,
        account_number=account_number,
        bank_name=bank_name,
        bank_code=bank_code,
        recipient_code=recipient_code,
        is_verified=True,
        is_primary=set_as_primary,
    )

    logger.info(
        f'Bank account added — business: {business.name} | '
        f'bank: {bank_name} | account: ****{account_number[-4:]} | '
        f'recipient: {recipient_code}'
    )

    return redirect('orders:bank_accounts')


# ============================================================
# BANK ACCOUNTS — REMOVE (soft delete)
# Sets is_active=False instead of deleting
# Preserves withdrawal history audit trail
# ============================================================
@require_POST
@login_required
def remove_bank_account(request, account_id):
    """
    Deactivate a bank account.
    Uses soft delete — record is preserved for audit trail.
    """
    account = get_object_or_404(
        BankAccount,
        id=account_id,
        business__owner=request.user,
    )

    # Prevent removing the only verified primary account
    # if vendor has pending withdrawals
    if account.is_primary:
        other_active = BankAccount.objects.filter(
            business=account.business,
            is_active=True,
        ).exclude(id=account.id).exists()

        if not other_active:
            pending_withdrawals = WithdrawalRequest.objects.filter(
                business=account.business,
                status__in=(
                    'pending_audit', 'pending_hold',
                    'pending_approval', 'approved', 'processing'
                ),
            ).exists()

            if pending_withdrawals:
                return render(request, 'orders/bank_accounts.html', {
                    'businesses': Business.objects.filter(
                        owner=request.user
                    ).prefetch_related('bank_accounts'),
                    'error': (
                        'Cannot remove your only bank account while '
                        'a withdrawal is in progress.'
                    ),
                })

    account.is_active = False
    account.is_primary = False
    account.save(update_fields=['is_active', 'is_primary', 'updated_at'])

    logger.info(
        f'Bank account deactivated — business: {account.business.name} | '
        f'account: ****{account.account_number[-4:]}'
    )

    return redirect('orders:bank_accounts')


# ============================================================
# WITHDRAWAL PAGE
# Shows vendor wallet balance + saved bank accounts
# Entry point before requesting withdrawal
# ============================================================
@login_required
def withdrawal_page(request):
    businesses = list(Business.objects.filter(
        owner=request.user
    ).select_related('wallet').prefetch_related('bank_accounts'))

    config = PlatformConfig.get_config()

    # Attach primary account directly to each business object
    # Template accesses it cleanly as business.primary_account
    for business in businesses:
        business.primary_account = business.bank_accounts.filter(
            is_primary=True,
            is_active=True,
            is_verified=True,
        ).first()

    return render(request, 'orders/withdrawal.html', {
        'businesses': businesses,
        'minimum_withdrawal': config.minimum_withdrawal_amount,
        'hold_days': config.withdrawal_hold_days,
    })


# ============================================================
# REQUEST WITHDRAWAL
#
# Full production flow:
#   1. Validate amount against PlatformConfig
#   2. Check vendor has verified primary bank account
#   3. Check sufficient wallet balance
#   4. Calculate hold_expires_at from PlatformConfig
#   5. Debit wallet as hold
#   6. Create Transaction record
#   7. Create WithdrawalRequest record
#   8. Run audit immediately
#   9. If approved → call Paystack Transfer API
#  10. Return correct status page based on outcome
# ============================================================
@require_POST
@login_required
def request_withdrawal(request):

    business_id = request.POST.get('business_id')

    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    # Load policy values from database
    config = PlatformConfig.get_config()
    minimum_withdrawal = config.minimum_withdrawal_amount
    hold_days = config.withdrawal_hold_days

    def _render_withdrawal_error(error):
        """Helper to re-render withdrawal page with error message."""
        businesses = Business.objects.filter(
            owner=request.user
        ).select_related('wallet').prefetch_related('bank_accounts')
        return render(request, 'orders/withdrawal.html', {
            'businesses': businesses,
            'minimum_withdrawal': minimum_withdrawal,
            'hold_days': hold_days,
            'error': error,
        })

    # --- Validate amount ---
    try:
        amount_to_withdraw = Decimal(str(request.POST.get('amount', '0')))
    except (TypeError, ValueError, InvalidOperation):
        return _render_withdrawal_error(
            'Invalid amount. Please enter a valid number.'
        )

    if amount_to_withdraw <= 0:
        return _render_withdrawal_error(
            'Withdrawal amount must be greater than zero.'
        )

    if amount_to_withdraw < minimum_withdrawal:
        return _render_withdrawal_error(
            f'Minimum withdrawal amount is ₦{minimum_withdrawal:,.2f}.'
        )

    # --- Check vendor has a verified primary bank account ---
    primary_account = BankAccount.objects.filter(
        business=business,
        is_primary=True,
        is_active=True,
        is_verified=True,
    ).first()

    if not primary_account:
        return _render_withdrawal_error(
            'You do not have a verified primary bank account. '
            'Please add and verify a bank account before withdrawing.'
        )

    try:
        with transaction.atomic():

            # Lock wallet row
            wallet = Wallet.objects.select_for_update().get(business=business)

            if wallet.balance < amount_to_withdraw:
                return _render_withdrawal_error(
                    f'Insufficient funds. '
                    f'Your available balance is ₦{wallet.balance:,.2f}.'
                )

            # Calculate hold expiry
            # hold_expires_at = now + withdrawal_hold_days from PlatformConfig
            hold_expires_at = timezone.now() + timezone.timedelta(days=hold_days)

            # Debit wallet immediately as hold
            debit_wallet(
                business=business,
                amount=amount_to_withdraw,
                user=request.user,
                description=(
                    f'Withdrawal hold — ₦{amount_to_withdraw:,.2f} '
                    f'pending transfer to {primary_account.bank_name} '
                    f'****{primary_account.account_number[-4:]}.'
                ),
            )

            # Create withdrawal transaction record
            withdrawal_txn = Transaction.objects.create(
                user=request.user,
                business=business,
                amount=amount_to_withdraw,
                transaction_type='withdrawal',
                status='pending',
                description=(
                    f'Vendor withdrawal to {primary_account.bank_name} '
                    f'****{primary_account.account_number[-4:]}'
                ),
            )

            # Create WithdrawalRequest record
            withdrawal_request = WithdrawalRequest.objects.create(
                business=business,
                bank_account=primary_account,
                transaction=withdrawal_txn,
                amount=amount_to_withdraw,
                status='pending_audit',
                hold_expires_at=hold_expires_at,
            )

            logger.info(
                f'WithdrawalRequest created — business: {business.name} | '
                f'amount: ₦{amount_to_withdraw:,.2f} | '
                f'hold_expires_at: {hold_expires_at} | '
                f'request: #{withdrawal_request.id}'
            )

    except Wallet.DoesNotExist:
        logger.error(
            f'Withdrawal failed — wallet not found for business: {business.id}'
        )
        return _render_withdrawal_error(
            'Wallet not found. Please contact support.'
        )

    except Exception as e:
        logger.error(
            f'Withdrawal creation failed — business: {business.id} | '
            f'amount: {amount_to_withdraw} | error: {e}'
        )
        return _render_withdrawal_error(
            'Something went wrong. Please try again.'
        )

    # ----------------------------------------------------------
    # RUN AUDIT — outside the atomic block
    # The WithdrawalRequest is now committed to the database.
    # The audit reads it, runs all checks, and updates its status.
    # Running outside atomic means audit failures don't roll back
    # the WithdrawalRequest creation — we want the record preserved
    # even if the audit blocks it.
    # ----------------------------------------------------------
    from transactions.audit import run_withdrawal_audit
    audit_result = run_withdrawal_audit(withdrawal_request)

    # Refresh from database — audit has updated the status
    withdrawal_request.refresh_from_db()

    # ----------------------------------------------------------
    # AUDIT FAILED — funds already reversed by audit service
    # Show vendor the reason
    # ----------------------------------------------------------
    if not audit_result['passed']:
        return render(request, 'orders/withdrawal_blocked.html', {
            'withdrawal_request': withdrawal_request,
            'reason': audit_result['notes'],
        })

    # ----------------------------------------------------------
    # AUDIT PASSED — check current status and act accordingly
    # ----------------------------------------------------------

    # Pending hold — hold period active, transfer will fire automatically later
    if withdrawal_request.status == 'pending_hold':
        return render(request, 'orders/withdrawal_initiated.html', {
            'withdrawal_request': withdrawal_request,
            'txn': withdrawal_txn,
            'business': business,
            'hold_expires_at': withdrawal_request.hold_expires_at,
        })

    # Pending approval — flagged for admin review
    if withdrawal_request.status == 'pending_approval':
        return render(request, 'orders/withdrawal_initiated.html', {
            'withdrawal_request': withdrawal_request,
            'txn': withdrawal_txn,
            'business': business,
        })

    # Approved — all gates passed, initiate transfer now
    if withdrawal_request.status == 'approved':
        _initiate_withdrawal_transfer(withdrawal_request, withdrawal_txn)
        withdrawal_request.refresh_from_db()

    return render(request, 'orders/withdrawal_initiated.html', {
        'withdrawal_request': withdrawal_request,
        'txn': withdrawal_txn,
        'business': business,
    })


# ============================================================
# HELPER — Initiate the actual Paystack bank transfer
#
# Called when a WithdrawalRequest reaches 'approved' status.
# Updates the request to 'processing' and calls Paystack.
# Webhook (transfer.success / transfer.failed) handles
# the final outcome.
# ============================================================
def _initiate_withdrawal_transfer(withdrawal_request, withdrawal_txn):
    """
    Call Paystack Transfer API for an approved withdrawal.
    Updates WithdrawalRequest status to processing.
    Final status update comes via webhook.
    """
    bank_account = withdrawal_request.bank_account

    transfer_response = initiate_transfer(
        amount=withdrawal_request.amount,
        recipient_code=bank_account.recipient_code,
        reference=withdrawal_txn.reference,
        reason=f'PayFusion vendor payout — {withdrawal_request.business.name}',
    )

    if transfer_response.get('status') is True:
        transfer_code = transfer_response['data'].get('transfer_code')

        withdrawal_request.status = 'processing'
        withdrawal_request.transfer_code = transfer_code
        withdrawal_request.processing_started_at = timezone.now()
        withdrawal_request.save(update_fields=[
            'status',
            'transfer_code',
            'processing_started_at',
            'updated_at',
        ])

        logger.info(
            f'Transfer initiated — business: {withdrawal_request.business.name} | '
            f'amount: ₦{withdrawal_request.amount:,.2f} | '
            f'transfer_code: {transfer_code}'
        )

    else:
        # Transfer initiation failed — mark as failed, reverse wallet
        withdrawal_request.status = 'failed'
        withdrawal_request.rejection_reason = (
            f'Transfer initiation failed: '
            f'{transfer_response.get("message", "Unknown error")}'
        )
        withdrawal_request.save(update_fields=[
            'status',
            'rejection_reason',
            'updated_at',
        ])

        from transactions.services import credit_wallet
        credit_wallet(
            business=withdrawal_request.business,
            amount=withdrawal_request.amount,
            description=(
                f'Reversal: Transfer initiation failed for '
                f'withdrawal #{withdrawal_request.id}'
            ),
        )

        logger.error(
            f'Transfer initiation failed — business: {withdrawal_request.business.name} | '
            f'response: {transfer_response.get("message")}'
        )