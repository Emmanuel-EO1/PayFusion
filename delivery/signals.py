import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from orders.models import Order

logger = logging.getLogger('payfusion')


# ============================================================
# SIGNAL — Auto-create Delivery record when Order is paid
#
# Fires every time an Order instance is saved.
# Only acts when:
#   1. The order already exists (not just created) — update=True
#   2. The status has just changed to 'paid'
#
# Why post_save and not a direct call in the webhook:
#   The webhook marks the order paid and saves it.
#   post_save fires after that save completes.
#   This keeps the webhook handler clean — it doesn't need
#   to know about delivery logic. The signal handles it
#   automatically regardless of what triggered the status change.
#
# Why check 'paid' specifically:
#   Orders start as 'pending'. Only paid orders need a delivery
#   record. We never create delivery records for pending or
#   cancelled orders.
#
# Why get_or_create:
#   Protects against duplicate signals. If somehow the signal
#   fires twice for the same order, get_or_create ensures
#   only one Delivery record is ever created.
# ============================================================
@receiver(post_save, sender=Order)
def create_delivery_on_payment(sender, instance, created, **kwargs):
    """
    Automatically creates a Delivery record when an Order
    is marked as paid.

    Args:
        sender:   The Order model class
        instance: The specific Order being saved
        created:  True if this is a brand new Order record
        **kwargs: Additional signal arguments (required by Django)
    """
    # Only act on updates (status changes), not new order creation.
    # A newly created order is always 'pending' — never 'paid'.
    if created:
        return

    # Only create a delivery record when status is paid
    if instance.status != 'paid':
        return

    # get_or_create: create the delivery record if it doesn't
    # exist, or retrieve the existing one if it does.
    # The boolean 'delivery_created' tells us which happened.
    delivery, delivery_created = (
        # Import here to avoid circular imports at module level.
        # delivery.models imports orders.models, and orders.models
        # is already imported above. Importing Delivery at the top
        # would create a circular dependency on startup.
        __import__(
            'delivery.models',
            fromlist=['Delivery']
        ).Delivery.objects.get_or_create(order=instance)
    )

    if delivery_created:
        # Log the creation for audit trail
        logger.info(
            f'Delivery record created — '
            f'order: {instance.reference} | '
            f'vendor: {instance.business.name}'
        )

        # Create the first history entry
        from delivery.models import DeliveryStatusHistory
        DeliveryStatusHistory.objects.create(
            delivery=delivery,
            status='pending',
            note='Order payment confirmed. Awaiting vendor action.',
        )