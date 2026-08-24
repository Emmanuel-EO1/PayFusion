import logging
import itertools

from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.db.models import Q

from tenants.models import Business, VendorStorefront
from .models import Product, ProductCategory, ProductVariant, VariantAttribute, Tag

logger = logging.getLogger('payfusion')


def product_list(request):
    products = Product.objects.all()
    return render(request, 'products/product_list.html', {'products': products})


def product_detail(request, id):
    from products.models import Review
    product = get_object_or_404(Product, id=id)
    user_has_reviewed = (
        request.user.is_authenticated and
        Review.objects.filter(user=request.user, product=product).exists()
    )
    return render(request, 'products/product_detail.html', {
        'product': product,
        'user_has_reviewed': user_has_reviewed,
    })


@login_required
def manage_products(request):
    businesses = Business.objects.filter(
        owner=request.user
    ).prefetch_related('products__category', 'products__tags')

    if not businesses.exists():
        return redirect('core:home')

    search_query = request.GET.get('q', '').strip()
    tag_filter = request.GET.get('tag', '').strip()

    business_products = []
    for business in businesses:
        products = business.products.prefetch_related('tags').all()

        if search_query:
            products = products.filter(
                Q(name__icontains=search_query) | Q(sku__icontains=search_query)
            )

        if tag_filter:
            normalised_tag = ' '.join(
                tag_filter.lower().replace('-', ' ').split()
            )
            products = products.filter(tags__name=normalised_tag)

        business_products.append({
            'business': business,
            'products': products,
        })

    return render(request, 'products/manage_products.html', {
        'business_products': business_products,
        'search_query': search_query,
        'tag_filter': tag_filter,
    })


@login_required
def create_product(request, business_id):
    """
    Creates a new product for a vendor's business.
    Works as a standalone form (Add Product button) or
    receives pre-filled data from the AI description generator.

    GET  → show blank or pre-filled form
    POST → validate and save product to database
    """
    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    categories = ProductCategory.objects.all()

    if request.method == 'GET':
        return render(request, 'products/create_product.html', {
            'business': business,
            'categories': categories,
            'draft': {},
        })

    # POST — read every field the vendor submitted
    name = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    price_raw = request.POST.get('price', '').strip()
    sku = request.POST.get('sku', '').strip()
    stock_quantity_raw = request.POST.get('stock_quantity', '0').strip()
    low_stock_threshold_raw = request.POST.get('low_stock_threshold', '5').strip()
    category_id = request.POST.get('category', '').strip()
    is_active = request.POST.get('is_active') == 'on'
    tags_raw = request.POST.get('tags', '').strip()
    tag_names = [t.strip() for t in tags_raw.split(',') if t.strip()]

    errors = []

    if not name:
        errors.append('Product name is required.')

    try:
        price = Decimal(price_raw)
        if price <= 0:
            errors.append('Price must be greater than zero.')
    except (InvalidOperation, TypeError):
        errors.append('Please enter a valid price.')
        price = Decimal('0.00')

    try:
        stock_quantity = int(stock_quantity_raw)
        if stock_quantity < 0:
            errors.append('Stock quantity cannot be negative.')
    except (ValueError, TypeError):
        errors.append('Please enter a valid stock quantity.')
        stock_quantity = 0

    try:
        low_stock_threshold = int(low_stock_threshold_raw)
        if low_stock_threshold < 0:
            errors.append('Low stock threshold cannot be negative.')
    except (ValueError, TypeError):
        errors.append('Please enter a valid low stock threshold.')
        low_stock_threshold = 5

    if sku:
        if Product.objects.filter(sku=sku).exists():
            errors.append(f'SKU "{sku}" is already used by another product.')

    category = None
    if category_id:
        category = ProductCategory.objects.filter(id=category_id).first()

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'products/create_product.html', {
            'business': business,
            'categories': categories,
            'draft': {
                'title': name,
                'description': description,
                'tags': tags_raw,
            },
        })

    # All validation passed — save the product
    product = Product.objects.create(
        business=business,
        name=name,
        description=description,
        price=price,
        sku=sku or None,
        stock_quantity=stock_quantity,
        low_stock_threshold=low_stock_threshold,
        category=category,
        is_active=is_active,
    )

    new_tags = []
    for tag_name in tag_names:
        tag = Tag.get_or_create_normalised(tag_name)
        if tag:
            new_tags.append(tag)
    product.tags.set(new_tags)

    logger.info(
        f'Product created — business: {business.name} | '
        f'product: {product.name} | by: {request.user.email}'
    )

    messages.success(request, f'{product.name} created successfully.')
    return redirect('products:manage_products')


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

    name = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    price_raw = request.POST.get('price', '').strip()
    sku = request.POST.get('sku', '').strip()
    stock_quantity_raw = request.POST.get('stock_quantity', '').strip()
    low_stock_threshold_raw = request.POST.get('low_stock_threshold', '').strip()
    category_id = request.POST.get('category', '').strip()
    is_active = request.POST.get('is_active') == 'on'
    tags_raw = request.POST.get('tags', '').strip()
    tag_names = [t.strip() for t in tags_raw.split(',') if t.strip()]

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

    if sku:
        sku_taken = Product.objects.filter(sku=sku).exclude(id=product.id).exists()
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

    new_tags = []
    for tag_name in tag_names:
        tag = Tag.get_or_create_normalised(tag_name)
        if tag:
            new_tags.append(tag)
    product.tags.set(new_tags)

    if stock_quantity != previous_stock:
        logger.info(
            f'Stock manually updated — product: {product.name} | '
            f'{previous_stock} -> {stock_quantity} | '
            f'by: {request.user.email}'
        )

    messages.success(request, f'{product.name} updated successfully.')
    return redirect('products:manage_products')


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


ATTRIBUTE_ROW_COUNT = 4


def _build_attribute_rows(existing_attributes=None):
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
    pairs = []
    for i in range(1, ATTRIBUTE_ROW_COUNT + 1):
        name = post_data.get(f'attr_name_{i}', '').strip()
        value = post_data.get(f'attr_value_{i}', '').strip()
        if name and value:
            pairs.append((name, value))
    return pairs


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


@login_required
def bulk_generate_variants(request, product_id):
    product = get_object_or_404(
        Product,
        id=product_id,
        business__owner=request.user,
    )

    if request.method == 'GET':
        return render(request, 'products/bulk_generate_variants.html', {
            'product': product,
            'attribute_rows': range(1, ATTRIBUTE_ROW_COUNT + 1),
        })

    attribute_sets = []
    for i in range(1, ATTRIBUTE_ROW_COUNT + 1):
        name = request.POST.get(f'attribute_name_{i}', '').strip()
        values_raw = request.POST.get(f'attribute_values_{i}', '').strip()

        if not name or not values_raw:
            continue

        values = [v.strip() for v in values_raw.split(',') if v.strip()]
        if values:
            attribute_sets.append((name, values))

    if not attribute_sets:
        messages.error(
            request,
            'Please provide at least one attribute with values.'
        )
        return render(request, 'products/bulk_generate_variants.html', {
            'product': product,
            'attribute_rows': range(1, ATTRIBUTE_ROW_COUNT + 1),
        })

    attribute_names = [name for name, _ in attribute_sets]
    value_lists = [values for _, values in attribute_sets]
    combinations = list(itertools.product(*value_lists))

    existing_combinations = set()
    for variant in product.variants.prefetch_related('attributes').all():
        combo = tuple(
            sorted((a.name, a.value) for a in variant.attributes.all())
        )
        existing_combinations.add(combo)

    created_count = 0
    skipped_count = 0

    for combo_values in combinations:
        combo_pairs = list(zip(attribute_names, combo_values))
        combo_key = tuple(sorted(combo_pairs))

        if combo_key in existing_combinations:
            skipped_count += 1
            continue

        variant = ProductVariant.objects.create(
            product=product,
            stock_quantity=0,
            price_adjustment=Decimal('0.00'),
            is_active=True,
        )

        for name, value in combo_pairs:
            VariantAttribute.objects.create(
                variant=variant,
                name=name,
                value=value,
            )

        existing_combinations.add(combo_key)
        created_count += 1

    logger.info(
        f'Bulk variant generation — product: {product.name} | '
        f'created: {created_count} | skipped: {skipped_count} | '
        f'by: {request.user.email}'
    )

    if created_count:
        messages.success(
            request,
            f'{created_count} variant(s) created. '
            f'{skipped_count} already existed and were skipped.'
        )
    else:
        messages.info(
            request,
            f'No new variants — all {skipped_count} combination(s) already existed.'
        )

    return redirect('products:bulk_edit_variants', product_id=product.id)


@login_required
def bulk_edit_variants(request, product_id):
    product = get_object_or_404(
        Product,
        id=product_id,
        business__owner=request.user,
    )

    variants = list(
        product.variants.prefetch_related('attributes').order_by('id')
    )

    if request.method == 'GET':
        return render(request, 'products/bulk_edit_variants.html', {
            'product': product,
            'variants': variants,
        })

    errors = []
    updates = []

    for variant in variants:
        stock_raw = request.POST.get(f'stock_quantity_{variant.id}', '').strip()
        price_adj_raw = request.POST.get(f'price_adjustment_{variant.id}', '0').strip()
        sku = request.POST.get(f'sku_{variant.id}', '').strip()
        is_active = request.POST.get(f'is_active_{variant.id}') == 'on'

        try:
            stock_quantity = int(stock_raw)
            if stock_quantity < 0:
                errors.append(f'{variant.display_name}: stock cannot be negative.')
                continue
        except (ValueError, TypeError):
            errors.append(f'{variant.display_name}: invalid stock value.')
            continue

        try:
            price_adjustment = Decimal(price_adj_raw or '0')
        except InvalidOperation:
            errors.append(f'{variant.display_name}: invalid price adjustment.')
            continue

        if sku:
            sku_taken = ProductVariant.objects.filter(
                sku=sku
            ).exclude(id=variant.id).exists()
            if sku_taken:
                errors.append(f'{variant.display_name}: SKU "{sku}" already used.')
                continue

        updates.append({
            'variant': variant,
            'stock_quantity': stock_quantity,
            'price_adjustment': price_adjustment,
            'sku': sku or None,
            'is_active': is_active,
        })

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'products/bulk_edit_variants.html', {
            'product': product,
            'variants': variants,
        })

    for update in updates:
        variant = update['variant']
        previous_stock = variant.stock_quantity

        variant.stock_quantity = update['stock_quantity']
        variant.price_adjustment = update['price_adjustment']
        variant.sku = update['sku']
        variant.is_active = update['is_active']
        variant.save()

        if update['stock_quantity'] != previous_stock:
            logger.info(
                f'Bulk stock update — variant: {variant.display_name} | '
                f'{previous_stock} -> {update["stock_quantity"]} | '
                f'by: {request.user.email}'
            )

    messages.success(request, f'{len(updates)} variant(s) updated.')
    return redirect('products:manage_variants', product_id=product.id)


@login_required
def storefront_settings(request, business_id):
    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    storefront, _ = VendorStorefront.objects.get_or_create(business=business)
    own_products = Product.objects.filter(
        business=business,
        is_active=True,
    ).order_by('name')

    if request.method == 'GET':
        return render(request, 'products/storefront_settings.html', {
            'business': business,
            'storefront': storefront,
            'own_products': own_products,
        })

    tagline = request.POST.get('tagline', '').strip()
    accent_colour = request.POST.get('accent_colour', '#3b82f6').strip()
    featured_ids = request.POST.getlist('featured_products')
    remove_banner = request.POST.get('remove_banner') == 'on'

    errors = []

    if len(tagline) > 200:
        errors.append('Tagline must be 200 characters or fewer.')

    if not accent_colour.startswith('#') or len(accent_colour) not in (4, 7):
        errors.append('Accent colour must be a valid hex code, e.g. #3b82f6.')

    if len(featured_ids) > 6:
        errors.append('You can feature a maximum of 6 products.')

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'products/storefront_settings.html', {
            'business': business,
            'storefront': storefront,
            'own_products': own_products,
        })

    storefront.tagline = tagline
    storefront.accent_colour = accent_colour

    if remove_banner and storefront.banner_image:
        storefront.banner_image.delete(save=False)
        storefront.banner_image = None

    if 'banner_image' in request.FILES:
        storefront.banner_image = request.FILES['banner_image']

    storefront.save()

    featured = Product.objects.filter(
        id__in=featured_ids,
        business=business,
        is_active=True,
    )
    storefront.featured_products.set(featured)

    logger.info(
        f'Storefront updated — business: {business.name} | '
        f'by: {request.user.email}'
    )

    messages.success(request, 'Storefront updated successfully.')
    return redirect('products:storefront_settings', business_id=business.id)


@login_required
def generate_description(request, business_id):
    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    categories = ProductCategory.objects.all()

    if request.method == 'GET':
        return render(request, 'products/generate_description.html', {
            'business': business,
            'categories': categories,
        })

    if 'product_image' not in request.FILES:
        messages.error(request, 'Please upload a product image.')
        return render(request, 'products/generate_description.html', {
            'business': business,
            'categories': categories,
        })

    image_file = request.FILES['product_image']

    allowed_types = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')
    if image_file.content_type not in allowed_types:
        messages.error(request, 'Please upload a JPEG, PNG, WebP, or GIF image.')
        return render(request, 'products/generate_description.html', {
            'business': business,
            'categories': categories,
        })

    if image_file.size > 5 * 1024 * 1024:
        messages.error(request, 'Image must be under 5MB.')
        return render(request, 'products/generate_description.html', {
            'business': business,
            'categories': categories,
        })

    try:
        from products.ai_service import generate_product_content_from_image
        draft = generate_product_content_from_image(image_file)
        messages.success(
            request,
            'AI draft generated. Review and edit below, then save your product.'
        )
    except ValueError as e:
        messages.error(request, str(e))
        return render(request, 'products/generate_description.html', {
            'business': business,
            'categories': categories,
        })

    # Render create_product template directly with AI draft pre-filled
    return render(request, 'products/create_product.html', {
        'business': business,
        'categories': categories,
        'draft': draft,
        'from_ai': True,
    })

# ============================================================
# REVIEWS (Phase 11 Step 2)
# ============================================================

def _user_can_review(user, product):
    from orders.models import OrderItem
    item = OrderItem.objects.filter(
        product=product,
        order__user=user,
        order__status='paid',
        order__delivery__status='delivered',
    ).select_related('order').first()
    return item.order if item else None


@login_required
def submit_review(request, product_id):
    from products.models import Review
    product = get_object_or_404(Product, id=product_id, is_active=True)

    existing = Review.objects.filter(user=request.user, product=product).first()
    if existing:
        messages.info(request, 'You have already reviewed this product.')
        return redirect('products:product_detail', id=product_id)

    delivered_order = _user_can_review(request.user, product)
    if not delivered_order:
        messages.error(request, 'You can only review products you have purchased and received.')
        return redirect('products:product_detail', id=product_id)

    if request.method == 'GET':
        return render(request, 'products/submit_review.html', {'product': product})

    rating = request.POST.get('rating', '').strip()
    body = request.POST.get('body', '').strip()

    errors = []
    if not rating or not rating.isdigit() or int(rating) not in range(1, 6):
        errors.append('Please select a rating between 1 and 5.')
    if not body:
        errors.append('Please write a review.')
    if len(body) < 10:
        errors.append('Review must be at least 10 characters.')

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'products/submit_review.html', {'product': product})

    Review.objects.create(
        product=product,
        user=request.user,
        order=delivered_order,
        rating=int(rating),
        body=body,
    )

    messages.success(request, 'Your review has been submitted.')
    return redirect('products:product_detail', id=product_id)


@login_required
def edit_review(request, review_id):
    from products.models import Review
    review = get_object_or_404(Review, id=review_id, user=request.user)

    if request.method == 'GET':
        return render(request, 'products/edit_review.html', {'review': review})

    rating = request.POST.get('rating', '').strip()
    body = request.POST.get('body', '').strip()

    errors = []
    if not rating or not rating.isdigit() or int(rating) not in range(1, 6):
        errors.append('Please select a rating between 1 and 5.')
    if not body or len(body) < 10:
        errors.append('Review must be at least 10 characters.')

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'products/edit_review.html', {'review': review})

    review.rating = int(rating)
    review.body = body
    review.save(update_fields=['rating', 'body', 'updated_at'])

    messages.success(request, 'Review updated.')
    return redirect('products:product_detail', id=review.product.id)


@require_POST
@login_required
def submit_response(request, review_id):
    from products.models import Review, ReviewResponse
    review = get_object_or_404(
        Review,
        id=review_id,
        product__business__owner=request.user,
    )

    body = request.POST.get('body', '').strip()
    if not body:
        messages.error(request, 'Response cannot be empty.')
        return redirect('products:product_detail', id=review.product.id)

    ReviewResponse.objects.update_or_create(
        review=review,
        defaults={
            'vendor_business': review.product.business,
            'body': body,
        }
    )

    messages.success(request, 'Response posted.')
    return redirect('products:product_detail', id=review.product.id)


@require_POST
@login_required
def hide_review(request, review_id):
    from products.models import Review

    if not request.user.is_staff:
        messages.error(request, 'Permission denied.')
        return redirect('core:home')

    review = get_object_or_404(Review, id=review_id)
    review.is_hidden = not review.is_hidden
    review.save(update_fields=['is_hidden'])

    status = 'hidden' if review.is_hidden else 'visible'
    messages.success(request, f'Review marked as {status}.')
    return redirect('products:product_detail', id=review.product.id)