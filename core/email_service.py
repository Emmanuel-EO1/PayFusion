import logging
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger('payfusion')

FROM_EMAIL = settings.DEFAULT_FROM_EMAIL
SITE_URL = getattr(settings, 'SITE_URL', 'http://localhost:8000')


def _send(subject, text_body, html_template, context, recipient):
    """
    Internal helper — sends an email with both plain text and HTML versions.
    EmailMultiAlternatives sends plain text as the primary body,
    then attaches the HTML as an alternative. Email clients that support
    HTML show the HTML version; others fall back to plain text automatically.
    """
    try:
        html_body = render_to_string(html_template, context)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=FROM_EMAIL,
            to=[recipient],
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send()
        logger.info(f'Email sent: {subject} → {recipient}')
    except Exception as e:
        logger.error(f'Email failed: {subject} → {recipient} | {e}')


def send_payment_confirmation(user, transaction):
    from django.utils import timezone
    context = {
        'user': user,
        'amount': transaction.amount,
        'reference': transaction.reference,
        'date': timezone.now().strftime('%d %b %Y'),
        'site_url': SITE_URL,
    }
    _send(
        subject='Payment Confirmed — PayFusion',
        text_body=(
            f'Hi {user.username},\n\n'
            f'Your payment of N{transaction.amount} has been confirmed.\n'
            f'Reference: {transaction.reference}\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/payment_confirmation.html',
        context=context,
        recipient=user.email,
    )


def send_transfer_initiated(business, withdrawal):
    bank = withdrawal.bank_account
    context = {
        'business_name': business.name,
        'amount': withdrawal.amount,
        'bank_name': bank.bank_name if bank else '—',
        'account_last4': bank.account_number[-4:] if bank else '—',
        'reference': withdrawal.transaction.reference if withdrawal.transaction else '—',
        'site_url': SITE_URL,
    }
    _send(
        subject='Withdrawal Initiated — PayFusion',
        text_body=(
            f'Hi {business.name},\n\n'
            f'Your withdrawal of N{withdrawal.amount} has been initiated.\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/withdrawal_initiated.html',
        context=context,
        recipient=business.owner.email,
    )


def send_transfer_completed(business, withdrawal):
    bank = withdrawal.bank_account
    context = {
        'business_name': business.name,
        'amount': withdrawal.amount,
        'bank_name': bank.bank_name if bank else '—',
        'account_last4': bank.account_number[-4:] if bank else '—',
        'reference': withdrawal.transaction.reference if withdrawal.transaction else '—',
        'site_url': SITE_URL,
    }
    _send(
        subject='Withdrawal Completed — PayFusion',
        text_body=(
            f'Hi {business.name},\n\n'
            f'Your withdrawal of N{withdrawal.amount} has been completed.\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/withdrawal_completed.html',
        context=context,
        recipient=business.owner.email,
    )


def send_transfer_failed(business, withdrawal):
    context = {
        'business_name': business.name,
        'amount': withdrawal.amount,
        'reference': withdrawal.transaction.reference if withdrawal.transaction else '—',
        'site_url': SITE_URL,
    }
    _send(
        subject='Withdrawal Failed — PayFusion',
        text_body=(
            f'Hi {business.name},\n\n'
            f'Your withdrawal of N{withdrawal.amount} could not be processed. '
            f'The amount has been returned to your wallet.\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/withdrawal_failed.html',
        context=context,
        recipient=business.owner.email,
    )


def send_email_verification(user, verify_url):
    context = {
        'username': user.username,
        'verify_url': verify_url,
        'site_url': SITE_URL,
    }
    _send(
        subject='Verify your PayFusion email address',
        text_body=(
            f'Hi {user.username},\n\n'
            f'Please verify your email address:\n{verify_url}\n\n'
            f'This link expires in 7 days.\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/email_verification.html',
        context=context,
        recipient=user.email,
    )


def send_dispute_raised_vendor(business, dispute):
    context = {
        'business_name': business.name,
        'order_reference': dispute.order.reference,
        'dispute_id': dispute.id,
        'reason': dispute.reason,
        'business_id': business.id,
        'site_url': SITE_URL,
    }
    _send(
        subject=f'Dispute Raised on Order {dispute.order.reference} — PayFusion',
        text_body=(
            f'Hi {business.name},\n\n'
            f'A dispute has been raised on order {dispute.order.reference}.\n'
            f'Reason: {dispute.reason}\n\n'
            f'Our team will review within 7 days.\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/dispute_raised_vendor.html',
        context=context,
        recipient=business.owner.email,
    )


def send_dispute_resolved_customer(user, dispute):
    context = {
        'username': user.username,
        'order_reference': dispute.order.reference,
        'resolution': dispute.resolution,
        'amount': dispute.order.total_amount,
        'refund_amount': dispute.refund_amount,
        'site_url': SITE_URL,
    }
    _send(
        subject=f'Your Dispute Has Been Resolved — PayFusion',
        text_body=(
            f'Hi {user.username},\n\n'
            f'Your dispute for order {dispute.order.reference} has been resolved.\n'
            f'Resolution: {dispute.get_resolution_display()}\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/dispute_resolved_customer.html',
        context=context,
        recipient=user.email,
    )


def send_delivery_shipped(user, delivery):
    context = {
        'username': user.username,
        'business_name': delivery.order.business.name,
        'order_reference': delivery.order.reference,
        'courier': delivery.courier,
        'tracking_id': delivery.tracking_id,
        'estimated_delivery_date': (
            delivery.estimated_delivery_date.strftime('%d %b %Y')
            if delivery.estimated_delivery_date else None
        ),
        'site_url': SITE_URL,
    }
    _send(
        subject=f'Your Order Has Been Shipped — PayFusion',
        text_body=(
            f'Hi {user.username},\n\n'
            f'Your order {delivery.order.reference} has been shipped.\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/delivery_shipped.html',
        context=context,
        recipient=user.email,
    )


def send_review_received_vendor(business, review):
    context = {
        'business_name': business.name,
        'product_name': review.product.name,
        'product_id': review.product.id,
        'rating': review.rating,
        'review_body': review.body,
        'site_url': SITE_URL,
    }
    _send(
        subject=f'New Review on {review.product.name} — PayFusion',
        text_body=(
            f'Hi {business.name},\n\n'
            f'A customer left a {review.rating}★ review on {review.product.name}.\n'
            f'"{review.body}"\n\n'
            f'— PayFusion'
        ),
        html_template='core/emails/review_received_vendor.html',
        context=context,
        recipient=business.owner.email,
    )