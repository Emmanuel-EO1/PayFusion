import logging

from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST

from tenants.models import Business
from .models import Product, ProductCategory, ProductVariant, VariantAttribute

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


# ============================================================
# VENDOR — VARIANT LIST
#
# Shows all variants for one product, with stock and price
# adjustment visible at a glance. Entry point for adding new
# variants or editing existing ones.
# ============================================================
@login_required
def manage_variants(request, product_id):
    product = get_object_or_404(
        Product,
        id=product_id,
        business__owner=request.user,
    )

    variants = product.variants.prefetch_related('attributes').all()

    return render(request, 'products/manage_variants.html', {
        'product': product,
        'variants': variants,
    })


# Number of attribute name/value row pairs the form supports.
# Comfortably covers Size + Colour + Material + one more
# without needing a JavaScript-driven dynamic formset.
ATTRIBUTE_ROW_COUNT = 4


def _build_attribute_rows(existing_attributes=None):
    """
    Builds a list of simple objects for the variant_form template,
    each with .index, .name, .value — one per form row.
    Pre-fills name/value from existing_attributes when editing,
    leaves them blank for a fresh add form.
    Returns exactly ATTRIBUTE_ROW_COUNT rows regardless of how
    many existing attributes there are (extra existing attributes
    beyond the row count are simply not shown — not expected in
    practice since variants are created through this same form).
    """
    existing_attributes = existing_attributes or []

    class Row:
        def __init__(self, index, name='', value=''):
            self.index = index
            self.name = name
            self.value = value

    rows = []
    for i in range(1, ATTRIBUTE_ROW_COUNT + 1):
        if i <= len(existing_attributes):
            attr = existing_attributes[i - 1]
            rows.append(Row(i, attr.name, attr.value))
        else:
            rows.append(Row(i))

    return rows


def _extract_attributes_from_post(post_data):
    """
    Reads attribute name/value pairs from POST data using the
    fixed row pattern attr_name_1/attr_value_1 .. attr_name_4/attr_value_4.
    Rows where either field is blank are skipped — a vendor doesn't
    have to fill in all four rows, just the ones they need.
    Returns a list of (name, value) tuples.
    """
    pairs = []
    for i in range(1, ATTRIBUTE_ROW_COUNT + 1):
        name = post_data.get(f'attr_name_{i}', '').strip()
        value = post_data.get(f'attr_value_{i}', '').strip()
        if name and value:
            pairs.append((name, value))
    return pairs


# ============================================================
# VENDOR — ADD VARIANT
#
# GET  → blank form with empty attribute rows
# POST → validate, create ProductVariant + its VariantAttribute rows
# ============================================================
@login_required
def add_variant(request, product_id):
    product = get_object_or_404(
        Product,
        id=product_id,
        business__owner=request.user,
    )

    if request.method == 'GET':
        return render(request, 'products/variant_form.html', {
            'product': product,
            'variant': None,
            'attribute_rows': _build_attribute_rows(),
        })

    sku = request.POST.get('sku', '').strip()
    price_adjustment_raw = request.POST.get('price_adjustment', '0').strip()
    stock_quantity_raw = request.POST.get('stock_quantity', '').strip()
    low_stock_threshold_raw = request.POST.get('low_stock_threshold', '').strip()
    is_active = request.POST.get('is_active') == 'on'

    errors = []

    try:
        price_adjustment = Decimal(price_adjustment_raw or '0')
    except InvalidOperation:
        errors.append('Please enter a valid price adjustment.')
        price_adjustment = Decimal('0.00')

    try:
        stock_quantity = int(stock_quantity_raw)
        if stock_quantity < 0:
            errors.append('Stock quantity cannot be negative.')
    except (ValueError, TypeError):
        errors.append('Please enter a valid stock quantity.')
        stock_quantity = 0

    try:
        low_stock_threshold = int(low_stock_threshold_raw or '5')
        if low_stock_threshold < 0:
            errors.append('Low stock threshold cannot be negative.')
    except (ValueError, TypeError):
        errors.append('Please enter a valid low stock threshold.')
        low_stock_threshold = 5

    if sku:
        sku_taken = ProductVariant.objects.filter(sku=sku).exists()
        if sku_taken:
            errors.append(f'SKU "{sku}" is already used by another variant.')

    attribute_pairs = _extract_attributes_from_post(request.POST)
    if not attribute_pairs:
        errors.append('Please specify at least one attribute (e.g. Size, Colour).')

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'products/variant_form.html', {
            'product': product,
            'variant': None,
            'attribute_rows': _build_attribute_rows(),
        })

    variant = ProductVariant.objects.create(
        product=product,
        sku=sku or None,
        price_adjustment=price_adjustment,
        stock_quantity=stock_quantity,
        low_stock_threshold=low_stock_threshold,
        is_active=is_active,
    )

    for name, value in attribute_pairs:
        VariantAttribute.objects.create(
            variant=variant,
            name=name,
            value=value,
        )

    logger.info(
        f'Variant created — product: {product.name} | '
        f'variant: {variant.display_name} | by: {request.user.email}'
    )

    messages.success(request, f'Variant added to {product.name}.')
    return redirect('products:manage_variants', product_id=product.id)


# ============================================================
# VENDOR — EDIT VARIANT
#
# GET  → form pre-filled with current values and attributes
# POST → validate and save changes, replacing attribute rows
# ============================================================
@login_required
def edit_variant(request, product_id, variant_id):
    product = get_object_or_404(
        Product,
        id=product_id,
        business__owner=request.user,
    )
    variant = get_object_or_404(
        ProductVariant,
        id=variant_id,
        product=product,
    )

    if request.method == 'GET':
        existing_attributes = list(variant.attributes.all())
        return render(request, 'products/variant_form.html', {
            'product': product,
            'variant': variant,
            'attribute_rows': _build_attribute_rows(existing_attributes),
        })

    sku = request.POST.get('sku', '').strip()
    price_adjustment_raw = request.POST.get('price_adjustment', '0').strip()
    stock_quantity_raw = request.POST.get('stock_quantity', '').strip()
    low_stock_threshold_raw = request.POST.get('low_stock_threshold', '').strip()
    is_active = request.POST.get('is_active') == 'on'

    errors = []

    try:
        price_adjustment = Decimal(price_adjustment_raw or '0')
    except InvalidOperation:
        errors.append('Please enter a valid price adjustment.')
        price_adjustment = variant.price_adjustment

    try:
        stock_quantity = int(stock_quantity_raw)
        if stock_quantity < 0:
            errors.append('Stock quantity cannot be negative.')
    except (ValueError, TypeError):
        errors.append('Please enter a valid stock quantity.')
        stock_quantity = variant.stock_quantity

    try:
        low_stock_threshold = int(low_stock_threshold_raw or '5')
        if low_stock_threshold < 0:
            errors.append('Low stock threshold cannot be negative.')
    except (ValueError, TypeError):
        errors.append('Please enter a valid low stock threshold.')
        low_stock_threshold = variant.low_stock_threshold

    if sku:
        sku_taken = ProductVariant.objects.filter(
            sku=sku
        ).exclude(id=variant.id).exists()
        if sku_taken:
            errors.append(f'SKU "{sku}" is already used by another variant.')

    attribute_pairs = _extract_attributes_from_post(request.POST)
    if not attribute_pairs:
        errors.append('Please specify at least one attribute (e.g. Size, Colour).')

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'products/variant_form.html', {
            'product': product,
            'variant': variant,
            'attribute_rows': _build_attribute_rows(list(variant.attributes.all())),
        })

    previous_stock = variant.stock_quantity

    variant.sku = sku or None
    variant.price_adjustment = price_adjustment
    variant.stock_quantity = stock_quantity
    variant.low_stock_threshold = low_stock_threshold
    variant.is_active = is_active
    variant.save()

    # Replace attribute rows entirely — simplest correct approach
    # given the fixed-row form design. Avoids reconciling partial
    # updates against existing rows.
    variant.attributes.all().delete()
    for name, value in attribute_pairs:
        VariantAttribute.objects.create(
            variant=variant,
            name=name,
            value=value,
        )

    if stock_quantity != previous_stock:
        logger.info(
            f'Variant stock manually updated — variant: {variant.display_name} | '
            f'{previous_stock} -> {stock_quantity} | by: {request.user.email}'
        )

    messages.success(request, f'Variant updated for {product.name}.')
    return redirect('products:manage_variants', product_id=product.id) 