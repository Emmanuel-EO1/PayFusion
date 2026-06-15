import logging

from django.db import transaction
from django.utils import timezone

from delivery.models import Delivery, DeliveryStatusHistory

logger = logging.getLogger('payfusion')

HAND_DELIVERY_KEYWORDS = ('hand delivery', 'hand', 'personal', 'self', '')

VALID_TRANSITIONS = {
    'pending':    ['processing', 'cancelled'],
    'processing': ['shipped', 'cancelled'],
    'shipped':    ['in_transit', 'delivered', 'cancelled'],
    'in_transit': ['delivered', 'returned', 'cancelled'],
    'delivered':  [],
    'cancelled':  [],
    'returned':   [],
}


def update_delivery_status(delivery, new_status, changed_by=None, note=None):
    """
    Transition a delivery to a new status.
    Validates the transition, updates timestamps, logs history.
    Raises ValueError on invalid transition or finalised delivery.

    When new_status == 'delivered':
      After the status update commits, release_escrow(order) is
      called to move the vendor's held funds for this order from
      escrow_balance to balance (available/withdrawable).
      If escrow was disabled at payment time (no hold entry exists),
      release_escrow() logs a warning and returns harmlessly.
      If escrow is frozen due to an active dispute, release is
      skipped — funds stay locked until dispute resolution.
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
        f'Delivery status updated - '
        f'order: {delivery.order.reference} | '
        f'{current_status} -> {new_status} | '
        f'by: {changed_by}'
    )

    # Release escrow on delivery confirmation.
    # Runs after the atomic block above has committed —
    # release_escrow() runs its own atomic transaction on the
    # wallet, separate from the delivery status transaction.
    # Keeping these separate means a failure in escrow release
    # does not roll back the delivery status change — the
    # delivery is correctly marked delivered regardless, and
    # any escrow issue is logged for admin investigation.
    if new_status == 'delivered':
        from transactions.services import release_escrow
        release_escrow(delivery.order)

    return delivery


def set_shipping_details(delivery, courier, tracking_id, estimated_delivery_date=None):
    """
    Saves shipping details and determines is_hand_delivery.
    Called in ship_order view before updating status to shipped.

    is_hand_delivery is True when:
      - No courier provided, OR
      - Courier matches hand delivery keywords
    This controls who can confirm delivery later.
    """
    courier_normalised = (courier or '').strip().lower()
    is_hand = courier_normalised in HAND_DELIVERY_KEYWORDS

    delivery.courier = courier
    delivery.tracking_id = tracking_id
    delivery.is_hand_delivery = is_hand
    if estimated_delivery_date:
        delivery.estimated_delivery_date = estimated_delivery_date

    delivery.save(update_fields=[
        'courier',
        'tracking_id',
        'is_hand_delivery',
        'estimated_delivery_date',
        'updated_at',
    ])

    return delivery


def can_vendor_confirm_delivery(delivery):
    """
    Returns True if the vendor is allowed to mark this
    delivery as delivered.
    Only permitted for hand deliveries — vendors who used
    a courier cannot confirm delivery on the customer's behalf.
    """
    return delivery.is_hand_delivery


def can_customer_confirm_delivery(delivery):
    """
    Returns True if the customer can confirm delivery.
    Customer can always confirm when status is in_transit.
    """
    return delivery.status == 'in_transit'