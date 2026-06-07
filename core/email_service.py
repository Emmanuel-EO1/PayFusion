import logging

from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger('payfusion')


# ============================================================
# INTERNAL HELPER — Send email
#
# Central sending function used by all notification functions.
# Handles errors gracefully — a failed email should never
# crash the main payment or withdrawal flow.
#
# Uses Django's send_mail which works with any backend
# configured in settings.py (SMTP, SendGrid, Mailgun, etc.)
# ============================================================
def _send(subject, message, recipient_email):
    """
    Send a plain text email to a single recipient.
    Logs success and failure — never raises exceptions.

    Args:
        subject:         Email subject line
        message:         Plain text email body
        recipient_email: Recipient email address
    """
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],
            fail_silently=False,
        )
        logger.info(
            f'Email sent — to: {recipient_email} | subject: {subject}'
        )
    except Exception as e:
        # Never crash the main flow because of a failed email
        # Log the error so we can investigate and resend manually
        logger.error(
            f'Email failed — to: {recipient_email} | '
            f'subject: {subject} | error: {e}'
        )


# ============================================================
# CUSTOMER EMAILS
# ============================================================

def send_payment_confirmation(user, transaction, orders):
    """
    Sent to customer after a successful payment.
    Lists all vendor orders included in the transaction.

    Args:
        user:        The customer User instance
        transaction: The master Transaction instance
        orders:      QuerySet of Order instances linked to transaction
    """
    order_lines = '\n'.join([
        f'  • {order.business.name} — ₦{order.total_amount:,.2f}'
        for order in orders
    ])

    message = f"""Hi {user.first_name or user.email},

Your payment has been confirmed.

Transaction Reference: {transaction.reference}
Total Amount: ₦{transaction.amount:,.2f}

Order Breakdown:
{order_lines}

Thank you for shopping with PayFusion.

— The PayFusion Team
"""

    _send(
        subject='Your PayFusion order is confirmed',
        message=message,
        recipient_email=user.email,
    )


# ============================================================
# VENDOR WITHDRAWAL EMAILS
# ============================================================

def send_withdrawal_received(business, withdrawal_request):
    """
    Sent to vendor when withdrawal request is received
    and is in the hold period.

    Args:
        business:           The vendor's Business instance
        withdrawal_request: The WithdrawalRequest instance
    """
    hold_date = withdrawal_request.hold_expires_at.strftime('%A, %B %d, %Y')
    bank = withdrawal_request.bank_account

    message = f"""Hi {business.owner.first_name or business.owner.email},

Your withdrawal request has been received.

Business:       {business.name}
Amount:         ₦{withdrawal_request.amount:,.2f}
Bank Account:   {bank.bank_name} ****{bank.account_number[-4:]}
Reference:      {withdrawal_request.transaction.reference}

Your funds are under our standard security hold period.
Transfer will be initiated automatically on {hold_date}.

You do not need to do anything — we will handle it from here.

— The PayFusion Team
"""

    _send(
        subject=f'Withdrawal received — funds release date: {hold_date}',
        message=message,
        recipient_email=business.owner.email,
    )


def send_withdrawal_blocked(business, withdrawal_request):
    """
    Sent to vendor when withdrawal is blocked by the audit.
    Includes the specific reason so they know what to fix.

    Args:
        business:           The vendor's Business instance
        withdrawal_request: The WithdrawalRequest instance
    """
    message = f"""Hi {business.owner.first_name or business.owner.email},

Your withdrawal request has been blocked.

Business:  {business.name}
Amount:    ₦{withdrawal_request.amount:,.2f}
Reference: {withdrawal_request.transaction.reference if withdrawal_request.transaction else 'N/A'}

Reason:
{withdrawal_request.rejection_reason}

Your funds have been returned to your wallet.

Once you have resolved the issue above, you can submit a new withdrawal request.
If you need assistance, please contact our support team.

— The PayFusion Team
"""

    _send(
        subject='Your PayFusion withdrawal was blocked',
        message=message,
        recipient_email=business.owner.email,
    )


def send_withdrawal_under_review(business, withdrawal_request):
    """
    Sent to vendor when withdrawal is flagged for admin review
    (large amount or suspicious activity detected).

    Args:
        business:           The vendor's Business instance
        withdrawal_request: The WithdrawalRequest instance
    """
    message = f"""Hi {business.owner.first_name or business.owner.email},

Your withdrawal request is currently under review by our team.

Business:  {business.name}
Amount:    ₦{withdrawal_request.amount:,.2f}
Reference: {withdrawal_request.transaction.reference}

Large withdrawals and flagged requests are reviewed within 24 hours.
You will be notified by email once the review is complete.

— The PayFusion Team
"""

    _send(
        subject='Your PayFusion withdrawal is under review',
        message=message,
        recipient_email=business.owner.email,
    )


def send_transfer_initiated(business, withdrawal_request):
    """
    Sent to vendor when Paystack transfer is initiated —
    money is on its way to their bank.

    Args:
        business:           The vendor's Business instance
        withdrawal_request: The WithdrawalRequest instance
    """
    bank = withdrawal_request.bank_account

    message = f"""Hi {business.owner.first_name or business.owner.email},

Your withdrawal is on its way.

Business:     {business.name}
Amount:       ₦{withdrawal_request.amount:,.2f}
Bank Account: {bank.bank_name} ****{bank.account_number[-4:]}
Reference:    {withdrawal_request.transaction.reference}

Bank transfers typically take a few minutes to a few hours.
You will receive a confirmation email once the funds land.

— The PayFusion Team
"""

    _send(
        subject=f'Your ₦{withdrawal_request.amount:,.2f} withdrawal is on its way',
        message=message,
        recipient_email=business.owner.email,
    )


def send_transfer_completed(business, withdrawal_request):
    """
    Sent to vendor when transfer.success webhook confirms
    funds have reached the destination bank.

    Args:
        business:           The vendor's Business instance
        withdrawal_request: The WithdrawalRequest instance
    """
    bank = withdrawal_request.bank_account

    message = f"""Hi {business.owner.first_name or business.owner.email},

Your withdrawal has been completed successfully.

Business:     {business.name}
Amount:       ₦{withdrawal_request.amount:,.2f}
Bank Account: {bank.bank_name} ****{bank.account_number[-4:]}
Reference:    {withdrawal_request.transaction.reference}

The funds have been sent to your bank account.
Please allow a few hours for your bank to reflect the credit.

— The PayFusion Team
"""

    _send(
        subject=f'₦{withdrawal_request.amount:,.2f} successfully sent to your bank',
        message=message,
        recipient_email=business.owner.email,
    )


def send_transfer_failed(business, withdrawal_request):
    """
    Sent to vendor when transfer.failed webhook confirms
    the bank transfer was rejected. Funds are auto-reversed.

    Args:
        business:           The vendor's Business instance
        withdrawal_request: The WithdrawalRequest instance
    """
    bank = withdrawal_request.bank_account

    message = f"""Hi {business.owner.first_name or business.owner.email},

Unfortunately your bank transfer could not be completed.

Business:     {business.name}
Amount:       ₦{withdrawal_request.amount:,.2f}
Bank Account: {bank.bank_name} ****{bank.account_number[-4:]}
Reference:    {withdrawal_request.transaction.reference}

Your funds have been automatically returned to your PayFusion wallet.

This can happen due to:
  • Incorrect account details
  • Bank account restrictions
  • Temporary bank processing issues

Please verify your bank account details and try again.
If the issue persists, please contact our support team.

— The PayFusion Team
"""

    _send(
        subject=f'Transfer failed — ₦{withdrawal_request.amount:,.2f} returned to your wallet',
        message=message,
        recipient_email=business.owner.email,
    )