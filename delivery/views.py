import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.contrib import messages

from orders.models import Order
from tenants.models import Business
from delivery.models import Delivery
from delivery.services import update_delivery_status

logger = logging.getLogger('payfusion')

HAND_DELIVERY_KEYWORDS = ('hand delivery', 'hand-delivery', 'personal', 'self')


@login_required
def vendor_orders(request):
    businesses = Business.objects.filter(
        owner=request.user
    ).prefetch_related(
        'orders__delivery',
        'orders__items__product',
    )

    if not businesses.exists():
        return redirect('core:home')

    business_orders = []
    for business in businesses:
        orders = business.orders.filter(
            status='paid'
        ).select_related('delivery').order_by('-created_at')

        business_orders.append({
            'business': business,
            'orders': orders,
        })

    return render(request, 'delivery/vendor_orders.html', {
        'business_orders': business_orders,
    })


@require_POST
@login_required
def accept_order(request, order_id):
    order = get_object_or_404(
        Order,
        id=order_id,
        business__owner=request.user,
        status='paid',
    )

    delivery = get_object_or_404(Delivery, order=order)

    try:
        update_delivery_status(
            delivery=delivery,
            new_status='processing',
            changed_by=request.user,
            note='Order accepted by vendor. Preparing for dispatch.',
        )
        messages.success(request, f'Order {order.reference} accepted.')
    except ValueError as e:
        messages.error(request, str(e))

    return redirect('delivery:vendor_orders')


@require_POST
@login_required
def ship_order(request, order_id):
    order = get_object_or_404(
        Order,
        id=order_id,
        business__owner=request.user,
        status='paid',
    )

    delivery = get_object_or_404(Delivery, order=order)

    tracking_id = request.POST.get('tracking_id', '').strip()
    courier = request.POST.get('courier', '').strip()
    estimated_delivery_date = request.POST.get('estimated_delivery_date', '').strip()

    if not courier:
        messages.error(request, 'Please provide a courier name or select Hand Delivery.')
        return redirect('delivery:vendor_orders')

    # Courier-based delivery requires a tracking ID
    # Hand delivery does not
    is_hand = courier.lower() in HAND_DELIVERY_KEYWORDS

    if not is_hand and not tracking_id:
        messages.error(request, 'Please provide a tracking ID for courier delivery.')
        return redirect('delivery:vendor_orders')

    try:
        delivery.tracking_id = tracking_id if not is_hand else None
        delivery.courier = courier
        delivery.is_hand_delivery = is_hand
        if estimated_delivery_date:
            delivery.estimated_delivery_date = estimated_delivery_date

        delivery.save(update_fields=[
            'tracking_id',
            'courier',
            'is_hand_delivery',
            'estimated_delivery_date',
            'updated_at',
        ])

        note = (
            'Hand delivery by vendor.'
            if is_hand
            else f'Dispatched via {courier}. Tracking: {tracking_id}.'
        )

        update_delivery_status(
            delivery=delivery,
            new_status='shipped',
            changed_by=request.user,
            note=note,
        )
        messages.success(request, f'Order {order.reference} marked as shipped.')
    except ValueError as e:
        messages.error(request, str(e))

    return redirect('delivery:vendor_orders')


@require_POST
@login_required
def update_delivery(request, delivery_id):
    delivery = get_object_or_404(
        Delivery,
        id=delivery_id,
        order__business__owner=request.user,
    )

    new_status = request.POST.get('status', '').strip()
    note = request.POST.get('note', '').strip()

    if not new_status:
        messages.error(request, 'Please select a status.')
        return redirect('delivery:vendor_orders')

    # Vendor cannot mark delivered unless it is a hand delivery
    if new_status == 'delivered' and not delivery.is_hand_delivery:
        messages.error(
            request,
            'Only the customer can confirm delivery for courier-shipped orders. '
            'Please wait for the customer to confirm receipt.'
        )
        return redirect('delivery:vendor_orders')

    try:
        update_delivery_status(
            delivery=delivery,
            new_status=new_status,
            changed_by=request.user,
            note=note or None,
        )
        messages.success(request, f'Delivery status updated to {new_status}.')
    except ValueError as e:
        messages.error(request, str(e))

    return redirect('delivery:vendor_orders')


@login_required
def track_order(request, order_reference):
    order = get_object_or_404(
        Order,
        reference=order_reference,
        user=request.user,
        status='paid',
    )

    delivery = get_object_or_404(Delivery, order=order)
    history = delivery.history.select_related('changed_by').all()

    return render(request, 'delivery/track_order.html', {
        'order': order,
        'delivery': delivery,
        'history': history,
    })


@require_POST
@login_required
def confirm_delivery(request, order_reference):
    order = get_object_or_404(
        Order,
        reference=order_reference,
        user=request.user,
        status='paid',
    )

    delivery = get_object_or_404(Delivery, order=order)

    try:
        update_delivery_status(
            delivery=delivery,
            new_status='delivered',
            changed_by=request.user,
            note='Delivery confirmed by customer.',
        )
        messages.success(request, 'Thank you for confirming your delivery.')
    except ValueError as e:
        messages.error(request, str(e))

    return redirect('delivery:track_order', order_reference=order_reference)