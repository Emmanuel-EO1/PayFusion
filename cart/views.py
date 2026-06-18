from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from products.models import Product, ProductVariant
from django.views.decorators.http import require_POST
from .cart import Cart
from .forms import CartAddProductForm


@require_POST
def cart_add(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    form = CartAddProductForm(request.POST)

    if form.is_valid():
        cd = form.cleaned_data
        variant = None

        if product.has_variants:
            # A variant selection is mandatory for varianted products.
            # Without one, we don't know which size/colour to sell,
            # so we reject the add rather than guessing.
            variant_id = cd.get('variant_id')
            if not variant_id:
                messages.error(request, 'Please select an option before adding to cart.')
                return redirect('products:product_detail', id=product.id)

            variant = get_object_or_404(
                ProductVariant,
                id=variant_id,
                product=product,
                is_active=True,
            )

            if variant.stock_quantity < cd['quantity']:
                messages.error(
                    request,
                    f'Only {variant.stock_quantity} left for {variant.display_name}.'
                )
                return redirect('products:product_detail', id=product.id)

        else:
            # Simple product — validate against product-level stock
            if product.stock_quantity < cd['quantity']:
                messages.error(
                    request,
                    f'Only {product.stock_quantity} left for {product.name}.'
                )
                return redirect('products:product_detail', id=product.id)

        cart.add(
            product=product,
            quantity=cd['quantity'],
            override_quantity=cd['override'],
            variant=variant,
        )

    return redirect('cart:cart_detail')


@require_POST
def cart_remove(request, product_id, variant_id=None):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)

    variant = None
    if variant_id:
        variant = get_object_or_404(ProductVariant, id=variant_id, product=product)

    cart.remove(product, variant=variant)

    return redirect('cart:cart_detail')


@require_POST
def cart_update(request, product_id, variant_id=None):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)

    variant = None
    if variant_id:
        variant = get_object_or_404(ProductVariant, id=variant_id, product=product)

    new_quantity = int(request.POST.get('quantity', 1))

    # Validate against the correct stock source
    available = variant.stock_quantity if variant else product.stock_quantity
    if new_quantity > available:
        messages.error(
            request,
            f'Only {available} available. Quantity adjusted to maximum.'
        )
        new_quantity = available

    cart.add(
        product=product,
        quantity=new_quantity,
        override_quantity=True,
        variant=variant,
    )

    return redirect('cart:cart_detail')


def cart_clear(request):
    cart = Cart(request)
    cart.clear()
    return redirect('cart:cart_detail')


def cart_detail(request):
    cart = Cart(request)

    for item in cart:
        item['update_quantity_form'] = CartAddProductForm(initial={
            'quantity': item['quantity'],
            'override': True,
            'variant_id': item.get('variant_id'),
        })

    return render(request, 'cart/cart_detail.html', {'cart': cart})