import logging

from django.db import transaction
from django.utils import timezone

from delivery.models import Delivery, DeliveryStatusHistory

logger = logging.getLogger('payfusion')

VALID_TRANSITIONS = {
    'pending':    ['processing', 'cancelled'],
    'processing': ['shipped', 'cancelled'],
    'shipped':    ['in_transit', 'delivered', 'cancelled'],
    'in_transit': ['delivered', 'returned'],
    'delivered':  [],
    'cancelled':  [],
    'returned':   [],
}


def update_delivery_status(delivery, new_status, changed_by=None, note=None):
    """
    Transition a delivery to a new status.
    Validates the transition, updates timestamps, logs history.
    Raises ValueError on invalid transition or finalised delivery.
    """
    current_status = delivery.status

    if not delivery.is_active:
        raise ValueError(
            f'Delivery for order {delivery.order.reference} is '
            f'finalised ({current_status}) and cannot be updated.'
        )

    allowed = VALID_TRANSITIONS.get(current_status, [])
    if new_status not in allowed:
        raise ValueError(
            f'Cannot transition delivery from '
            f'{current_status} to {new_status}. '
            f'Allowed: {allowed or "none (finalised)"}.'
        )

    with transaction.atomic():
        delivery = Delivery.objects.select_for_update().get(id=delivery.id)

        delivery.status = new_status

        now = timezone.now()
        if new_status == 'processing':
            delivery.accepted_at = now
        elif new_status == 'shipped':
            delivery.shipped_at = now
        elif new_status == 'delivered':
            delivery.delivered_at = now
        elif new_status == 'cancelled':
            delivery.cancelled_at = now

        delivery.save(update_fields=[
            'status',
            'accepted_at',
            'shipped_at',
            'delivered_at',
            'cancelled_at',
            'updated_at',
        ])

        DeliveryStatusHistory.objects.create(
            delivery=delivery,
            status=new_status,
            changed_by=changed_by,
            note=note,
        )

    logger.info(
        f'Delivery status updated — '
        f'order: {delivery.order.reference} | '
        f'{current_status} -> {new_status} | '
        f'by: {changed_by}'
    )

    return delivery