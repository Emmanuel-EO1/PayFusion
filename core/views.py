from collections import defaultdict
from decimal import Decimal

from django.shortcuts import render, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from django.utils import timezone

from orders.models import Order


def home(request):
    return render(request, 'core/home.html')


# ============================================================
# PUBLIC VENDOR STOREFRONT
#
# Accessible to anyone — no login required.
# Shows the vendor's banner, tagline, featured products,
# and full active product catalogue.
# ============================================================
def vendor_storefront(request, slug):
    from tenants.models import Business
    from products.models import Product

    business = get_object_or_404(Business, slug=slug)
    storefront = business.storefront

    featured = storefront.featured_products.filter(
        is_active=True
    ).prefetch_related('tags')[:6]

    all_products = Product.objects.filter(
        business=business,
        is_active=True,
    ).prefetch_related('tags', 'variants').order_by('-created_at')

    return render(request, 'core/vendor_storefront.html', {
        'business': business,
        'storefront': storefront,
        'featured': featured,
        'products': all_products,
    })


# ============================================================
# COMMISSION ANALYTICS
#
# Staff-only dashboard showing platform commission revenue.
# All figures come from Order.commission_fee — the snapshot
# saved at settlement time, so historical accuracy is preserved
# regardless of later rate changes.
#
# Sections:
#   - Total commission (all time, this month, this week)
#   - Top 10 vendors by commission contribution
#   - Monthly trend (last 12 months)
#   - Breakdown by product category
# ============================================================
@staff_member_required
def commission_analytics(request):

    paid_orders = Order.objects.filter(
        status='paid',
        commission_fee__isnull=False,
    )

    total_commission = paid_orders.aggregate(
        total=Sum('commission_fee')
    )['total'] or Decimal('0.00')

    now = timezone.now()

    start_of_month = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    start_of_week = (now - timezone.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    this_month = paid_orders.filter(
        created_at__gte=start_of_month
    ).aggregate(total=Sum('commission_fee'))['total'] or Decimal('0.00')

    this_week = paid_orders.filter(
        created_at__gte=start_of_week
    ).aggregate(total=Sum('commission_fee'))['total'] or Decimal('0.00')

    # Top 10 vendors by commission contribution
    by_vendor = (
        paid_orders
        .values('business__name')
        .annotate(total=Sum('commission_fee'), order_count=Count('id'))
        .order_by('-total')[:10]
    )

    # Monthly trend — last 12 months
    by_month = (
        paid_orders
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(total=Sum('commission_fee'))
        .order_by('-month')[:12]
    )

    # By category — grouped in Python since commission_fee
    # is stored per-order, not per-item. Category is taken
    # from each order's first item.
    category_totals = defaultdict(lambda: {'total': Decimal('0.00'), 'count': 0})

    for order in paid_orders.prefetch_related('items__product__category'):
        first_item = order.items.first()
        category_name = 'Uncategorised'
        if first_item and first_item.product.category:
            category_name = first_item.product.category.name

        category_totals[category_name]['total'] += order.commission_fee or Decimal('0.00')
        category_totals[category_name]['count'] += 1

    by_category = sorted(
        category_totals.items(),
        key=lambda item: item[1]['total'],
        reverse=True,
    )

    return render(request, 'core/commission_analytics.html', {
        'total_commission': total_commission,
        'this_month': this_month,
        'this_week': this_week,
        'total_orders_count': paid_orders.count(),
        'by_vendor': by_vendor,
        'by_month': by_month,
        'by_category': by_category,
    })