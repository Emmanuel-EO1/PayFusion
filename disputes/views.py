import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.utils import timezone

from disputes.models import Dispute, DisputeEvidence
from orders.models import Order

logger = logging.getLogger('payfusion')


def _within_dispute_window(order):
    """
    Returns True if customer is still within the dispute window.
    Checks delivered_at on the delivery record against
    PlatformConfig.dispute_window_days.
    """
    from core.models import PlatformConfig
    from delivery.models import Delivery

    try:
        delivery = order.delivery
    except Delivery.DoesNotExist:
        return False

    if delivery.status != 'delivered' or not delivery.delivered_at:
        return False

    config = PlatformConfig.get_config()
    window_days = config.dispute_window_days
    deadline = delivery.delivered_at + timezone.timedelta(days=window_days)
    return timezone.now() <= deadline


@login_required
def raise_dispute(request, order_reference):
    order = get_object_or_404(
        Order,
        reference=order_reference,
        user=request.user,
        status='paid',
    )

    # Check dispute window
    if not _within_dispute_window(order):
        messages.error(
            request,
            'The dispute window for this order has closed. '
            'You can no longer raise a dispute.'
        )
        return redirect('users:transaction_history')

    # Check no existing dispute
    existing = Dispute.objects.filter(order=order).first()
    if existing:
        messages.info(request, 'A dispute already exists for this order.')
        return redirect('disputes:dispute_detail', dispute_id=existing.id)

    if request.method == 'GET':
        return render(request, 'disputes/raise_dispute.html', {
            'order': order,
        })

    reason = request.POST.get('reason', '').strip()
    evidence_note = request.POST.get('evidence_note', '').strip()
    files = request.FILES.getlist('evidence_files')

    errors = []
    if not reason:
        errors.append('Please describe the reason for your dispute.')
    if len(reason) < 20:
        errors.append('Please provide more detail — at least 20 characters.')
    if len(files) > 3:
        errors.append('You can upload a maximum of 3 files.')

    allowed_types = (
        'image/jpeg', 'image/png', 'image/webp',
        'application/pdf',
    )
    for f in files:
        if f.content_type not in allowed_types:
            errors.append(f'{f.name}: only JPEG, PNG, WebP, or PDF files allowed.')
        if f.size > 5 * 1024 * 1024:
            errors.append(f'{f.name}: file must be under 5MB.')

    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, 'disputes/raise_dispute.html', {'order': order})

    dispute = Dispute.objects.create(
        order=order,
        raised_by=request.user,
        business=order.business,
        transaction=order.transaction,
        reason=reason,
        evidence_note=evidence_note,
        status='open',
    )

    for f in files:
        DisputeEvidence.objects.create(dispute=dispute, file=f)

    logger.info(
        f'Dispute raised — order: {order.reference} | '
        f'by: {request.user.email}'
    )

    messages.success(
        request,
        'Your dispute has been raised. We will review it within '
        '7 days and notify you of the outcome.'
    )
    return redirect('disputes:dispute_detail', dispute_id=dispute.id)


@login_required
def dispute_detail(request, dispute_id):
    dispute = get_object_or_404(
        Dispute,
        id=dispute_id,
        raised_by=request.user,
    )

    evidence_files = dispute.evidence_files.all()

    return render(request, 'disputes/dispute_detail.html', {
        'dispute': dispute,
        'evidence_files': evidence_files,
    })


@login_required
def my_disputes(request):
    disputes = Dispute.objects.filter(
        raised_by=request.user,
    ).select_related('order', 'business').order_by('-created_at')

    return render(request, 'disputes/my_disputes.html', {
        'disputes': disputes,
    })

@login_required
def vendor_disputes(request, business_id):
    """
    Vendor sees all disputes raised against their business orders.
    Read-only — vendors cannot resolve disputes, only view and respond.
    """
    from tenants.models import Business
    business = get_object_or_404(
        Business,
        id=business_id,
        owner=request.user,
    )

    disputes = Dispute.objects.filter(
        business=business,
    ).select_related('order', 'raised_by').order_by('-created_at')

    return render(request, 'disputes/vendor_disputes.html', {
        'business': business,
        'disputes': disputes,
    })


@require_POST
@login_required
def add_vendor_note(request, dispute_id):
    """
    Vendor adds a note/their side of the story to a dispute.
    Only available while dispute is open or under_review.
    """
    from tenants.models import Business

    dispute = get_object_or_404(
        Dispute,
        id=dispute_id,
        business__owner=request.user,
    )

    if dispute.status == 'resolved':
        messages.error(request, 'Cannot add a note to a resolved dispute.')
        return redirect('disputes:vendor_disputes', business_id=dispute.business.id)

    note = request.POST.get('vendor_note', '').strip()
    if not note:
        messages.error(request, 'Note cannot be empty.')
        return redirect('disputes:vendor_disputes', business_id=dispute.business.id)

    dispute.vendor_note = note
    dispute.save(update_fields=['vendor_note'])

    messages.success(request, 'Your note has been added to the dispute.')
    return redirect('disputes:vendor_disputes', business_id=dispute.business.id)
