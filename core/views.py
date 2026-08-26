from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Avg
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.core.paginator import Paginator

from orders.models import Order


def home(request):
    from products.models import Product
    from orders.models import OrderItem

    # Trending — 6 most ordered products in last 7 days
    seven_days_ago = timezone.now() - timezone.timedelta(days=7)

    trending_ids = (
        OrderItem.objects
        .filter(
            order__status='paid',
            order__created_at__gte=seven_days_ago,
        )
        .values('product')
        .annotate(order_count=Count('id'))
        .order_by('-order_count')
        .values_list('product', flat=True)[:6]
    )

    trending_products = Product.objects.filter(
        id__in=trending_ids,
        is_active=True,
    )

    return render(request, 'core/home.html', {
        'trending_products': trending_products,
    })


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


def search(request):
    from products.models import Product, ProductCategory, Tag

    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '').strip()
    tag_slug = request.GET.get('tag', '').strip()
    min_price = request.GET.get('min_price', '').strip()
    max_price = request.GET.get('max_price', '').strip()
    in_stock = request.GET.get('in_stock', '')
    sort = request.GET.get('sort', 'newest')

    from django.db.models import Q

    products = Product.objects.filter(is_active=True).prefetch_related(
        'tags', 'variants', 'reviews'
    ).select_related('business', 'category')

    # Text search across name, description, tags
    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(tags__name__icontains=query)
        ).distinct()

    # Category filter
    if category_id:
        products = products.filter(category_id=category_id)

    # Tag filter
    if tag_slug:
        products = products.filter(tags__slug=tag_slug)

    # Price range filter
    if min_price:
        try:
            products = products.filter(price__gte=Decimal(min_price))
        except InvalidOperation:
            pass

    if max_price:
        try:
            products = products.filter(price__lte=Decimal(max_price))
        except InvalidOperation:
            pass

    # In stock filter — excludes products with zero stock and no variants
    if in_stock:
        products = products.filter(
            Q(stock_quantity__gt=0) |
            Q(variants__stock_quantity__gt=0)
        ).distinct()

    # Sorting
    if sort == 'price_asc':
        products = products.order_by('price')
    elif sort == 'price_desc':
        products = products.order_by('-price')
    elif sort == 'most_reviewed':
        products = products.annotate(
            review_count=Count('reviews')
        ).order_by('-review_count')
    elif sort == 'highest_rated':
        products = products.annotate(
            avg_rating=Avg('reviews__rating')
        ).order_by('-avg_rating')
    else:
        products = products.order_by('-created_at')

    paginator = Paginator(products, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    categories = ProductCategory.objects.all()
    tags = Tag.objects.all()

    return render(request, 'core/search.html', {
        'page_obj': page_obj,
        'query': query,
        'category_id': category_id,
        'tag_slug': tag_slug,
        'min_price': min_price,
        'max_price': max_price,
        'in_stock': in_stock,
        'sort': sort,
        'categories': categories,
        'tags': tags,
        'result_count': paginator.count,
    })


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

    by_vendor = (
        paid_orders
        .values('business__name')
        .annotate(total=Sum('commission_fee'), order_count=Count('id'))
        .order_by('-total')[:10]
    )

    by_month = (
        paid_orders
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(total=Sum('commission_fee'))
        .order_by('-month')[:12]
    )

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