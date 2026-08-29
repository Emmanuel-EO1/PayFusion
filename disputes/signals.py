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
        _process_full_refund(instance, order)
        _restore_stock_for_order(order)

    elif instance.resolution == 'partial_refund':
        _process_partial_refund(instance, order)

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

    Restores variant stock when the order item references a
    variant, otherwise restores product-level stock.
    """
    from django.db import transaction
    from products.models import Product, ProductVariant

    with transaction.atomic():
        for order_item in order.items.all():

            if order_item.variant_id:
                variant = ProductVariant.objects.select_for_update().get(
                    id=order_item.variant_id
                )
                variant.stock_quantity += order_item.quantity
                variant.save(update_fields=['stock_quantity', 'updated_at'])

                logger.info(
                    f'Stock restored — variant: {variant.display_name} | '
                    f'quantity: +{order_item.quantity} | '
                    f'new stock: {variant.stock_quantity} | '
                    f'order: {order.reference} (full refund)'
                )

            else:
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

def _process_full_refund(dispute, order):
    """
    Full refund — entire order amount returned to customer.
    Calls Paystack refund API, permanently removes escrow
    from vendor wallet (escrow stays frozen, balance unchanged).
    """
    from transactions.service.paystack import initiate_refund
    from transactions.models import EscrowEntry

    try:
        reference = order.transaction.reference
        initiate_refund(reference)

        # Mark escrow entry as permanently resolved —
        # funds neither released to vendor nor available for withdrawal
        EscrowEntry.objects.filter(
            order=order,
            entry_type='hold',
        ).update(is_frozen=True)

        logger.info(
            f'Full refund processed — dispute #{dispute.id} | '
            f'order: {order.reference} | '
            f'amount: N{order.total_amount}'
        )

    except ValueError as e:
        logger.error(
            f'Full refund FAILED — dispute #{dispute.id} | '
            f'order: {order.reference} | error: {e} | '
            f'MANUAL ACTION REQUIRED: process refund via Paystack dashboard.'
        )


def _process_partial_refund(dispute, order):
    """
    Partial refund — refund_amount returned to customer,
    remainder released to vendor available balance.
    """
    from transactions.service.paystack import initiate_refund
    from transactions.services import unfreeze_and_release_escrow
    from decimal import Decimal

    refund_amount = dispute.refund_amount

    if not refund_amount:
        logger.error(
            f'Partial refund FAILED — dispute #{dispute.id} | '
            f'no refund_amount set on dispute. '
            f'MANUAL ACTION REQUIRED.'
        )
        return

    try:
        reference = order.transaction.reference
        initiate_refund(reference, amount=refund_amount)

        logger.info(
            f'Partial refund processed — dispute #{dispute.id} | '
            f'order: {order.reference} | '
            f'refunded: N{refund_amount}'
        )

        # Release remaining escrow to vendor
        # (remainder after refund belongs to vendor)
        unfreeze_and_release_escrow(order)

    except ValueError as e:
        logger.error(
            f'Partial refund FAILED — dispute #{dispute.id} | '
            f'order: {order.reference} | error: {e} | '
            f'MANUAL ACTION REQUIRED: process refund via Paystack dashboard.'
        )