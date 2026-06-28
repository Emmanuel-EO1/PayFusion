import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Sum, Count

from tenants.models import Business, WithdrawalRequest
from orders.models import Order, OrderItem
from transactions.models import Transaction

logger = logging.getLogger('payfusion')


@login_required
def dashboard(request):
    """
    Vendor dashboard — shows escrow/available/total wallet
    breakdown, revenue, order stats, and recent orders for
    each business owned.
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

        wallet = business.wallet if hasattr(business, 'wallet') else None

        dashboard_data.append({
            'business': business,
            'available_balance': wallet.balance if wallet else 0,
            'escrow_balance': wallet.escrow_balance if wallet else 0,
            'total_balance': wallet.total_balance if wallet else 0,
            'total_revenue': total_revenue,
            'total_orders': total_orders,
            'pending_orders': pending_orders,
            'recent_orders': orders[:5],
        })

    return render(request, 'users/dashboard.html', {
        'dashboard_data': dashboard_data,
    })


@login_required
def transaction_history(request):
    """
    Customer view — all payments made by this user.
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


# ============================================================
# VENDOR — BUSINESS ANALYTICS
#
# Revenue and order charts for a specific business.
# Separate page from the dashboard to avoid loading heavy
# aggregation data on every dashboard page load.
# ============================================================
@login_required
def business_analytics(request, business_id):
    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    from django.db.models import Sum, Count
    from django.db.models.functions import TruncDay
    from django.utils import timezone
    from orders.models import OrderItem

    thirty_days_ago = timezone.now() - timezone.timedelta(days=30)

    # Daily revenue — last 30 days
    daily_revenue = (
        business.orders
        .filter(status='paid', created_at__gte=thirty_days_ago)
        .annotate(day=TruncDay('created_at'))
        .values('day')
        .annotate(revenue=Sum('total_amount'))
        .order_by('day')
    )

    # Daily order count — last 30 days
    daily_orders = (
        business.orders
        .filter(status='paid', created_at__gte=thirty_days_ago)
        .annotate(day=TruncDay('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    # Top 10 products by revenue
    top_by_revenue = (
        OrderItem.objects
        .filter(order__business=business, order__status='paid')
        .values('product__name')
        .annotate(revenue=Sum('price'))
        .order_by('-revenue')[:10]
    )

    # Top 10 products by units sold
    top_by_units = (
        OrderItem.objects
        .filter(order__business=business, order__status='paid')
        .values('product__name')
        .annotate(units=Sum('quantity'))
        .order_by('-units')[:10]
    )

    # Serialise querysets to JSON-safe lists for Chart.js
    import json
    from decimal import Decimal

    def decimal_default(obj):
        if isinstance(obj, Decimal):
            return float(obj)
        raise TypeError

    revenue_labels = [entry['day'].strftime('%b %d') for entry in daily_revenue]
    revenue_data   = [float(entry['revenue']) for entry in daily_revenue]

    order_labels   = [entry['day'].strftime('%b %d') for entry in daily_orders]
    order_data     = [entry['count'] for entry in daily_orders]

    rev_product_labels = [entry['product__name'] for entry in top_by_revenue]
    rev_product_data   = [float(entry['revenue']) for entry in top_by_revenue]

    unit_product_labels = [entry['product__name'] for entry in top_by_units]
    unit_product_data   = [entry['units'] for entry in top_by_units]

    return render(request, 'users/business_analytics.html', {
        'business': business,
        'revenue_labels':       json.dumps(revenue_labels),
        'revenue_data':         json.dumps(revenue_data),
        'order_labels':         json.dumps(order_labels),
        'order_data':           json.dumps(order_data),
        'rev_product_labels':   json.dumps(rev_product_labels),
        'rev_product_data':     json.dumps(rev_product_data),
        'unit_product_labels':  json.dumps(unit_product_labels),
        'unit_product_data':    json.dumps(unit_product_data),
    })