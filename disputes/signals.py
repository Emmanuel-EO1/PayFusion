import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from disputes.models import Dispute

logger = logging.getLogger('payfusion')


@receiver(post_save, sender=Dispute)
def handle_dispute_escrow(sender, instance, created, **kwargs):
    """
    Manages escrow state based on dispute lifecycle.

    On creation:
      Freeze escrow for the disputed order — funds locked
      regardless of delivery status until dispute resolves.

    On resolution to 'resolved' with resolution='no_refund':
      Unfreeze and release escrow — vendor receives funds
      as if delivery was confirmed normally.

    On resolution to 'resolved' with resolution in
    ('refund_issued', 'partial_refund'):
      Escrow stays frozen. Logs that manual refund processing
      is required. Full Paystack refund automation comes in
      Phase 11 alongside the customer-facing dispute UI.
    """
    from transactions.services import freeze_escrow, unfreeze_and_release_escrow

    order = instance.order

    if created:
        freeze_escrow(order)
        return

    if instance.status != 'resolved':
        return

    if instance.resolution == 'no_refund':
        logger.info(
            f'Dispute #{instance.id} resolved (no_refund) — '
            f'releasing escrow for order {order.reference}'
        )
        unfreeze_and_release_escrow(order)

    elif instance.resolution == 'refund_issued':
        logger.warning(
            f'Dispute #{instance.id} resolved (refund_issued) — '
            f'escrow remains frozen for order {order.reference}. '
            f'MANUAL ACTION REQUIRED: process refund via Paystack dashboard. '
            f'Automated refund processing arrives in Phase 11.'
        )
        _restore_stock_for_order(order)

    elif instance.resolution == 'partial_refund':
        logger.warning(
            f'Dispute #{instance.id} resolved (partial_refund) — '
            f'escrow remains frozen for order {order.reference}. '
            f'MANUAL ACTION REQUIRED: process refund via Paystack dashboard. '
            f'Automated refund processing arrives in Phase 11. '
            f'Stock NOT restored — item was not returned.'
        )

    elif instance.resolution == 'escalated':
        logger.info(
            f'Dispute #{instance.id} escalated — '
            f'escrow remains frozen for order {order.reference}'
        )

def _restore_stock_for_order(order):
    """
    Restores stock for every item in an order whose dispute
    resolved with a full refund. The item is considered
    returned to the vendor, so stock should reflect that
    it is available to sell again.

    Only called for resolution='refund_issued'.
    partial_refund does NOT restore stock — the customer
    keeps the item in that case.
    """
    from django.db import transaction
    from products.models import Product

    with transaction.atomic():
        for order_item in order.items.all():
            product = Product.objects.select_for_update().get(
                id=order_item.product_id
            )
            product.stock_quantity += order_item.quantity
            product.save(update_fields=['stock_quantity', 'updated_at'])

            logger.info(
                f'Stock restored — product: {product.name} | '
                f'quantity: +{order_item.quantity} | '
                f'new stock: {product.stock_quantity} | '
                f'order: {order.reference} (full refund)'
            )