import logging

from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST

from tenants.models import Business
from .models import Product, ProductCategory

logger = logging.getLogger('payfusion')


def product_list(request):
    products = Product.objects.all()
    return render(request, 'products/product_list.html', {'products': products})


def product_detail(request, id):
    product = get_object_or_404(Product, id=id)
    return render(request, 'products/product_detail.html', {'product': product})


# ============================================================
# VENDOR — PRODUCT MANAGEMENT (list)
#
# Shows every product across all businesses owned by the
# logged-in vendor, grouped by business. Supports searching
# by name or SKU. This is the vendor-facing equivalent of
# Django admin's product list — restricted to their own
# products only.
# ============================================================
@login_required
def manage_products(request):
    businesses = Business.objects.filter(
        owner=request.user
    ).prefetch_related('products__category')

    if not businesses.exists():
        return redirect('core:home')

    search_query = request.GET.get('q', '').strip()

    business_products = []
    for business in businesses:
        products = business.products.all()

        if search_query:
            products = products.filter(
                models_q_name_or_sku(search_query)
            )

        business_products.append({
            'business': business,
            'products': products,
        })

    return render(request, 'products/manage_products.html', {
        'business_products': business_products,
        'search_query': search_query,
    })


def models_q_name_or_sku(query):
    """
    Small helper building a Q object that matches a product's
    name or sku against the search query, case-insensitive.
    Kept as a function rather than inline Q() chains repeated
    in multiple places, since search may extend to more fields
    later (e.g. description) without touching the view logic.
    """
    from django.db.models import Q
    return Q(name__icontains=query) | Q(sku__icontains=query)


# ============================================================
# VENDOR — EDIT / RESTOCK PRODUCT
#
# GET  → show edit form pre-filled with current values
# POST → validate and save changes
#
# This is the only place a vendor can update stock_quantity.
# Restocking is simply increasing this value through the form —
# no separate restock-specific endpoint, since it's the same
# field regardless of direction (increase or decrease).
# ============================================================
@login_required
def edit_product(request, product_id):
    product = get_object_or_404(
        Product,
        id=product_id,
        business__owner=request.user,
    )

    categories = ProductCategory.objects.all()

    if request.method == 'GET':
        return render(request, 'products/edit_product.html', {
            'product': product,
            'categories': categories,
        })

    # POST — process form submission
    name = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    price_raw = request.POST.get('price', '').strip()
    sku = request.POST.get('sku', '').strip()
    stock_quantity_raw = request.POST.get('stock_quantity', '').strip()
    low_stock_threshold_raw = request.POST.get('low_stock_threshold', '').strip()
    category_id = request.POST.get('category', '').strip()
    is_active = request.POST.get('is_active') == 'on'

    errors = []

    if not name:
        errors.append('Product name is required.')

    try:
        price = Decimal(price_raw)
        if price <= 0:
            errors.append('Price must be greater than zero.')
    except (InvalidOperation, TypeError):
        errors.append('Please enter a valid price.')
        price = product.price

    try:
        stock_quantity = int(stock_quantity_raw)
        if stock_quantity < 0:
            errors.append('Stock quantity cannot be negative.')
    except (ValueError, TypeError):
        errors.append('Please enter a valid stock quantity.')
        stock_quantity = product.stock_quantity

    try:
        low_stock_threshold = int(low_stock_threshold_raw)
        if low_stock_threshold < 0:
            errors.append('Low stock threshold cannot be negative.')
    except (ValueError, TypeError):
        errors.append('Please enter a valid low stock threshold.')
        low_stock_threshold = product.low_stock_threshold

    # SKU uniqueness check — excluding this product itself
    if sku:
        sku_taken = Product.objects.filter(
            sku=sku
        ).exclude(id=product.id).exists()
        if sku_taken:
            errors.append(f'SKU "{sku}" is already used by another product.')

    category = None
    if category_id:
        category = ProductCategory.objects.filter(id=category_id).first()

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'products/edit_product.html', {
            'product': product,
            'categories': categories,
        })

    previous_stock = product.stock_quantity

    product.name = name
    product.description = description
    product.price = price
    product.sku = sku or None
    product.stock_quantity = stock_quantity
    product.low_stock_threshold = low_stock_threshold
    product.category = category
    product.is_active = is_active
    product.save()

    if stock_quantity != previous_stock:
        logger.info(
            f'Stock manually updated — product: {product.name} | '
            f'{previous_stock} -> {stock_quantity} | '
            f'by: {request.user.email}'
        )

    messages.success(request, f'{product.name} updated successfully.')
    return redirect('products:manage_products')