import logging

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Sum, Count

from tenants.models import Business, WithdrawalRequest
from orders.models import Order, OrderItem
from transactions.models import Transaction

logger = logging.getLogger('payfusion')


# ============================================================
# VENDOR DASHBOARD
# ============================================================
@login_required
def dashboard(request):
    """
    Vendor dashboard — shows wallet balance, revenue,
    order stats, and recent orders for each business owned.
    Only accessible to users who own at least one business.
    """
    businesses = (
        Business.objects
        .filter(owner=request.user)
        .select_related('wallet')
        .prefetch_related(
            Prefetch(
                'orders',
                queryset=Order.objects.select_related(
                    'business'
                ).order_by('-created_at')
            )
        )
    )

    if not businesses.exists():
        return redirect('core:home')

    dashboard_data = []

    for business in businesses:
        orders = business.orders.all()

        total_revenue = orders.filter(status='paid').aggregate(
            total=Sum('total_amount')
        )['total'] or 0

        total_orders = orders.aggregate(
            count=Count('id')
        )['count'] or 0

        pending_orders = orders.filter(status='pending').count()

        dashboard_data.append({
            'business': business,
            'wallet_balance': business.wallet.balance if hasattr(business, 'wallet') else 0,
            'total_revenue': total_revenue,
            'total_orders': total_orders,
            'pending_orders': pending_orders,
            'recent_orders': orders[:5],
        })

    return render(request, 'users/dashboard.html', {
        'dashboard_data': dashboard_data,
    })


# ============================================================
# CUSTOMER TRANSACTION HISTORY
#
# Shows the logged-in customer every payment they have made.
# Groups by master transaction so multi-vendor checkouts
# appear as one payment with multiple vendor orders beneath.
# ============================================================
@login_required
def transaction_history(request):
    """
    Customer view — all payments made by this user.
    Each entry shows the master transaction with all
    vendor orders nested beneath it.
    """
    transactions = (
        Transaction.objects
        .filter(
            user=request.user,
            transaction_type='payment',
        )
        .prefetch_related(
            Prefetch(
                'orders',
                queryset=Order.objects.select_related(
                    'business'
                ).prefetch_related('items__product')
            )
        )
        .order_by('-created_at')
    )

    return render(request, 'users/transaction_history.html', {
        'transactions': transactions,
    })


# ============================================================
# VENDOR SALES HISTORY
#
# Shows the vendor all orders received across their businesses.
# Filters by the logged-in user's businesses only.
# ============================================================
@login_required
def sales_history(request):
    """
    Vendor view — orders received per business, separated by business.
    """
    businesses = (
        Business.objects
        .filter(owner=request.user)
        .prefetch_related(
            Prefetch(
                'orders',
                queryset=Order.objects
                    .select_related('user', 'transaction')
                    .prefetch_related('items__product')
                    .order_by('-created_at')
            )
        )
    )

    if not businesses.exists():
        return redirect('core:home')

    return render(request, 'users/sales_history.html', {
        'businesses': businesses,
    })


# ============================================================
# VENDOR PAYOUT HISTORY
#
# Shows the vendor all withdrawal requests they have made
# across all their businesses — with full status tracking.
# ============================================================
@login_required
def payout_history(request):
    """
    Vendor view — all withdrawal requests and their statuses.
    """
    businesses = Business.objects.filter(owner=request.user)

    if not businesses.exists():
        return redirect('core:home')

    withdrawals = (
        WithdrawalRequest.objects
        .filter(business__in=businesses)
        .select_related(
            'business',
            'bank_account',
            'transaction',
            'reviewed_by',
        )
        .order_by('-created_at')
    )

    return render(request, 'users/payout_history.html', {
        'withdrawals': withdrawals,
        'businesses': businesses,
    })