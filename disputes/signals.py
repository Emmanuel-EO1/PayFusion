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

    elif instance.resolution in ('refund_issued', 'partial_refund'):
        logger.warning(
            f'Dispute #{instance.id} resolved ({instance.resolution}) — '
            f'escrow remains frozen for order {order.reference}. '
            f'MANUAL ACTION REQUIRED: process refund via Paystack dashboard. '
            f'Automated refund processing arrives in Phase 11.'
        )

    elif instance.resolution == 'escalated':
        logger.info(
            f'Dispute #{instance.id} escalated — '
            f'escrow remains frozen for order {order.reference}'
        )