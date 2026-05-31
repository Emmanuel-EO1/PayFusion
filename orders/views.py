import uuid
from django.conf import settings
from django.db.models import F
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from transactions.service.paystack import (initialize_payment, verify_payment)
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import HttpResponseBadRequest
from tenants.models import Business
from tenants.models import Wallet
from .models import Order, OrderItem
from transactions.models import Transaction
from cart.cart import Cart
from transactions.services import credit_wallet, debit_wallet
from django.contrib.auth import get_user_model

User = get_user_model()

def generate_reference():
    while True:
        ref = uuid.uuid4().hex[:12].upper()
        if not Transaction.objects.filter(reference=ref).exists():
            return ref

@login_required
def checkout(request):
    cart = Cart(request)

    if len(cart) == 0:
        return redirect('cart:cart_detail')
    
    business_groups = {}

    for item in cart:
        product = item['product']

        if not product.business:
            return HttpResponseBadRequest('Invalid product')
        
        business = product.business

        if business.id not in business_groups:
            business_groups[business.id] = {
                'instance': business,
                'items': [],
                'total': Decimal('0.00')
            }

        price = product.price

        business_groups[business.id]['items'].append({
            'product': product,
            'price': price,
            'quantity': item['quantity']
        })

        business_groups[business.id]['total'] += price * item['quantity']

    with transaction.atomic():

        system_user, _ = User.objects.get_or_create(email='system@payfusion.com', defaults={'username': 'system', 'is_active': False, 'is_staff': False,})

        # 1. Fetch the platform Business
        platform_business, _ =Business.objects.get_or_create(
            slug='payfusion-system',
            defaults={'name': 'Payfusion Treasury', 'owner': system_user}
        )
        
        master_txn = Transaction.objects.create(
            user=request.user,
            business=platform_business,
            amount=cart.get_total_price(),
            status='pending',
            transaction_type='payment',
            reference=generate_reference()
        )
        
        for group in business_groups.values():

            order = Order.objects.create(
                user=request.user,
                business=group['instance'],
                transaction=master_txn,
                status='pending',
                total_amount=group['total']
            )

            for item in group['items']:
                OrderItem.objects.create(
                    order=order,
                    product=item['product'],
                    price=item['price'],
                    quantity=item['quantity']
                )

    cart.clear()

    return redirect('orders:payment_summary', transaction_id=master_txn.id)


@login_required
def payment_summary(request, transaction_id):
    txn = get_object_or_404(Transaction, id=transaction_id, user=request.user)

    orders = txn.orders.select_related('business').all()

    return render(request, 'orders/payment_summary.html', {
        'transaction': txn,
        'orders': orders
    })

@login_required
def initialize_paystack_payment(request, transaction_id):

    txn = get_object_or_404(
        Transaction,
        id=transaction_id,
        user=request.user
    )

    if txn.status == 'paid':
        return redirect(
            'orders:payment_summary',
            transaction_id=txn.id
        )

    response = initialize_payment(
        email=request.user.email,
        amount=txn.amount,
        reference=txn.reference
    )

    if response.get('status') is True:

        payment_url = response['data']['authorization_url']
        return redirect(payment_url)

    return HttpResponseBadRequest("Unable to initialize payment")


#@login_required
def payment_callback(request):

    reference = request.GET.get('reference')

    # User closed payment or payment interrupted
    if not reference:
        return render(request, 'orders/payment_failed.html', {
            'message': 'Payment was cancelled or interrupted.'
        })

    txn = get_object_or_404(
        Transaction,
        reference=reference
    )

    # Already settled by webhook
    if txn.status == 'paid':
        return render(request, 'orders/success.html', {
            'txn': txn
        })

    response = verify_payment(reference)

    # Could not verify
    if not response.get('status'):
        return render(request, 'orders/payment_processing.html', {
            'txn': txn
        })

    data = response['data']

    # Failed payment
    if data['status'] != 'success':

        txn.status = 'failed'
        txn.save(update_fields=['status'])

        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'Payment failed.'
        })

    # Amount validation
    amount_paid = Decimal(data['amount']) / Decimal('100')

    if amount_paid != txn.amount:

        txn.status = 'failed'
        txn.save(update_fields=['status'])

        return render(request, 'orders/payment_failed.html', {
            'txn': txn,
            'message': 'Amount mismatch detected.'
        })

    # WAIT for webhook settlement
    return render(request, 'orders/payment_processing.html', {
        'txn': txn
    })


@login_required
def payment_failed(request, transaction_id):

    txn = get_object_or_404(
        Transaction, 
        id=transaction_id,
        user=request.user
    )

    return render(request, 'orders/payment_failed.html', {'txn': txn})


@login_required
def withdrawal_page(request):
    businesses = Business.objects.filter(owner=request.user).select_related('wallet')

    return render(request, 'orders/withdrawal.html', {'businesses': businesses})

@require_POST
@login_required
def request_withdrawal(request):
    business_id = request.POST.get('business_id')

    # 1. Ensure the business actually belongs to the logged-in user
    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user
    )

    # 2. Safely parse and validate the requested amount
    try:
        amount_to_withdraw = Decimal(request.POST.get('amount'))
    except (TypeError, ValueError, InvalidOperation):
        return HttpResponseBadRequest("Invalid amount format")

    if amount_to_withdraw <= 0:
        return HttpResponseBadRequest("Amount must be greater than zero")

    # 3. Open a safe, isolated database transaction block
    with transaction.atomic():
        
        wallet = Wallet.objects.select_for_update().get(business=business)

        # Check if they actually have enough virtual cash
        if wallet.balance < amount_to_withdraw:
            return HttpResponseBadRequest("Insufficient funds")

        # 4. INSTANTLY debit the Vendor's Virtual Wallet balance.
        # This acts as a holding mechanism so they can't reuse this money.
        debit_wallet(
            business=business,
            amount=amount_to_withdraw,
            user=request.user,
            description=f"Withdrew {amount_to_withdraw} to external bank account (Pending Verification)."
        )

        txn = Transaction.objects.create(
            user=request.user,
            business=business,
            amount=amount_to_withdraw,
            transaction_type='withdrawal',
            status='pending',  # Crucial: This tracks that cash is traveling outside your system
            reference=f"WDL-{uuid.uuid4().hex[:10].upper()}",
            description="Withdrawal payout processing via banking channel"
        )
        
        # 6. HAND OFF TO BANKING / PAYSTACK API HERE
        # would initialize the Paystack Transfer recipient and transfer request here:
        # paystack_response = initiate_paystack_transfer(amount_to_withdraw, txn.reference, ...)
        
        return render(request, 'orders/withdrawal_initiated.html', {'txn': txn})

    return HttpResponseBadRequest("Failed to process withdrawal.")

    