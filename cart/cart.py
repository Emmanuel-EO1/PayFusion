from decimal import Decimal
from django.conf import settings
from products.models import Product, ProductVariant


class Cart:
    def __init__(self, request):
        self.session = request.session
        cart = self.session.get(settings.CART_SESSION_ID)
        if not cart:
            cart = self.session[settings.CART_SESSION_ID] = {}

        self.cart = cart

    def _make_key(self, product, variant=None):
        """
        Builds the cart's storage key.
        Simple products (no variant): key is just the product id.
        Variant purchases: key combines product id and variant id,
        so different variants of the same product are tracked
        as separate cart lines.
        """
        if variant:
            return f'{product.id}-{variant.id}'
        return str(product.id)

    def add(self, product, quantity=1, override_quantity=False, variant=None):
        """
        Adds a product (optionally a specific variant) to the cart.

        Args:
            product: the Product instance
            quantity: how many units to add
            override_quantity: if True, sets quantity exactly rather
                                than incrementing
            variant: optional ProductVariant instance. Required when
                     the product has variants (enforced by the view
                     layer, not here — cart stays simple).
        """
        key = self._make_key(product, variant)

        # Price stored at add-time: variant's final_price if a
        # variant is selected, otherwise the product's own price.
        price = variant.final_price if variant else product.price

        if key not in self.cart:
            self.cart[key] = {
                'quantity': 0,
                'price': str(price),
                'product_id': product.id,
                'variant_id': variant.id if variant else None,
            }

        if override_quantity:
            self.cart[key]['quantity'] = quantity
        else:
            self.cart[key]['quantity'] += quantity

        self.save()

    def save(self):
        self.session.modified = True

    def remove(self, product, variant=None):
        key = self._make_key(product, variant)

        if key in self.cart:
            del self.cart[key]
            self.save()

    def __iter__(self):
        # Collect product ids and variant ids referenced in the cart
        product_ids = {item['product_id'] for item in self.cart.values()}
        variant_ids = {
            item['variant_id'] for item in self.cart.values()
            if item.get('variant_id')
        }

        products = Product.objects.filter(id__in=product_ids)
        product_map = {p.id: p for p in products}

        variants = ProductVariant.objects.filter(
            id__in=variant_ids
        ).prefetch_related('attributes')
        variant_map = {v.id: v for v in variants}

        for key, item in self.cart.items():
            cart_item = item.copy()
            cart_item['product'] = product_map.get(item['product_id'])
            cart_item['variant'] = (
                variant_map.get(item['variant_id'])
                if item.get('variant_id') else None
            )
            cart_item['price'] = Decimal(cart_item['price'])
            cart_item['total'] = cart_item['price'] * cart_item['quantity']
            yield cart_item

    def __len__(self):
        return sum(item['quantity'] for item in self.cart.values())

    def get_total_price(self):
        return sum(Decimal(item['price']) * item['quantity'] for item in self.cart.values())

    def clear(self):
        self.session[settings.CART_SESSION_ID] = {}
        self.save()