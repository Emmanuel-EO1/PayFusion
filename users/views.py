from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from tenants.models import Business
from orders.models import Order
from django.db.models import Prefetch, Sum, Count

# Create your views here.

@login_required
def dashboard(request):
    # Get all businesses owned by this user
    businesses = (
        Business.objects
        .filter(owner=request.user)
        .select_related('wallet')  # Important: avoids extra queries
        .prefetch_related(
            Prefetch(
                'orders',
                queryset=Order.objects.select_related('business').order_by('-created_at')
            )
        )
    )

    # If not a vendor → redirect to marketplace
    if not businesses.exists():
        # return render(request, 'users/no_business.html')
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
            'recent_orders': orders[:5],  # limit for performance
        })

    return render(request, 'users/dashboard.html', {
        'dashboard_data': dashboard_data
    })