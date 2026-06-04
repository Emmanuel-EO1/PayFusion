import logging
import requests

from decimal import Decimal
from django.conf import settings

logger = logging.getLogger('payfusion')

# ============================================================
# PAYSTACK API ENDPOINTS
# Centralised here so if Paystack ever changes a URL,
# we update it in one place only.
# ============================================================
PAYSTACK_BASE_URL = 'https://api.paystack.co'

PAYSTACK_INITIALIZE_URL  = f'{PAYSTACK_BASE_URL}/transaction/initialize'
PAYSTACK_VERIFY_URL      = f'{PAYSTACK_BASE_URL}/transaction/verify'
PAYSTACK_TRANSFER_URL    = f'{PAYSTACK_BASE_URL}/transfer'
PAYSTACK_RECIPIENT_URL   = f'{PAYSTACK_BASE_URL}/transferrecipient'
PAYSTACK_BANKS_URL       = f'{PAYSTACK_BASE_URL}/bank'
PAYSTACK_RESOLVE_URL     = f'{PAYSTACK_BASE_URL}/bank/resolve'


# ============================================================
# INTERNAL HELPER — Build auth headers
# Every Paystack API call needs the same Authorization header.
# Centralising this means if we ever rotate the key,
# one change covers all functions.
# ============================================================
def _get_headers():
    return {
        'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
        'Content-Type': 'application/json',
    }


# ============================================================
# INTERNAL HELPER — Naira to Kobo conversion
#
# Paystack works exclusively in Kobo (smallest NGN unit).
# ₦1 = 100 Kobo. All amounts sent to Paystack must be in Kobo.
#
# WHY Decimal and not float:
#   float(1999.99) * 100 = 199998.99999999997  ← wrong
#   Decimal('1999.99') * 100 = 199999.00        ← correct
#
# We convert to int at the end because Paystack expects
# a whole integer — no decimal points in Kobo amounts.
# ============================================================
def _to_kobo(naira_amount):
    """Convert a Naira Decimal amount to Kobo integer for Paystack."""
    return int(Decimal(str(naira_amount)) * 100)


# ============================================================
# INFLOW — Initialize Payment
#
# Called when a customer is ready to pay.
# Creates a payment session on Paystack and returns
# an authorization_url to redirect the customer to.
#
# Returns a dict:
#   On success: {'status': True, 'data': {'authorization_url': '...', ...}}
#   On failure: {'status': False, 'message': '...'}
# ============================================================
def initialize_payment(email, amount, reference):
    """
    Initialize a Paystack payment session.

    Args:
        email:     Customer's email address
        amount:    Payment amount in Naira (Decimal)
        reference: Unique transaction reference (our reference, not Paystack's)

    Returns:
        dict: Paystack API response or structured error dict
    """
    payload = {
        'email': email,
        'amount': _to_kobo(amount),
        'reference': reference,
        'callback_url': settings.PAYSTACK_CALLBACK_URL,
        'currency': 'NGN',
        'channels': ['card', 'bank', 'ussd', 'qr', 'mobile_money', 'bank_transfer'],
    }

    try:
        response = requests.post(
            PAYSTACK_INITIALIZE_URL,
            json=payload,
            headers=_get_headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        # Paystack did not respond within 30 seconds
        logger.error(f'Paystack initialize timeout — reference: {reference}')
        return {'status': False, 'message': 'Payment gateway timeout. Please try again.'}

    except requests.exceptions.ConnectionError:
        # Could not reach Paystack servers at all
        logger.error(f'Paystack connection error during initialize — reference: {reference}')
        return {'status': False, 'message': 'Could not reach payment gateway. Please try again.'}

    except requests.exceptions.HTTPError as e:
        # Paystack returned a 4xx or 5xx status code
        logger.error(f'Paystack HTTP error during initialize: {e} — reference: {reference}')
        return {'status': False, 'message': 'Payment gateway error. Please try again.'}

    except Exception as e:
        # Catch-all for any other unexpected error
        logger.error(f'Unexpected error during payment initialize: {e} — reference: {reference}')
        return {'status': False, 'message': 'An unexpected error occurred.'}


# ============================================================
# INFLOW — Verify Payment
#
# Called in the payment callback to confirm a payment's status
# directly with Paystack before taking any action.
# This is the browser-redirect verification path.
# The webhook is the server-to-server path (more reliable).
# Using both ensures no payment is ever missed.
#
# Returns a dict:
#   On success: {'status': True, 'data': {'status': 'success', ...}}
#   On failure: {'status': False, 'message': '...'}
# ============================================================
def verify_payment(reference):
    """
    Verify a payment's status with Paystack using the reference.

    Args:
        reference: The transaction reference to verify

    Returns:
        dict: Paystack API response or structured error dict
    """
    try:
        response = requests.get(
            f'{PAYSTACK_VERIFY_URL}/{reference}',
            headers=_get_headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        logger.error(f'Paystack verify timeout — reference: {reference}')
        return {'status': False, 'message': 'Verification timeout. Please wait and check your transaction history.'}

    except requests.exceptions.ConnectionError:
        logger.error(f'Paystack connection error during verify — reference: {reference}')
        return {'status': False, 'message': 'Could not reach payment gateway.'}

    except requests.exceptions.HTTPError as e:
        logger.error(f'Paystack HTTP error during verify: {e} — reference: {reference}')
        return {'status': False, 'message': 'Verification error.'}

    except Exception as e:
        logger.error(f'Unexpected error during payment verify: {e} — reference: {reference}')
        return {'status': False, 'message': 'An unexpected error occurred.'}


# ============================================================
# OUTFLOW — Fetch Supported Banks
#
# Returns a list of all Nigerian banks supported by Paystack.
# Used to populate the bank selector on the withdrawal form
# so vendors can pick their bank from a verified list.
#
# Returns a dict:
#   On success: {'status': True, 'data': [...list of banks...]}
#   On failure: {'status': False, 'message': '...'}
# ============================================================
def get_banks():
    """
    Fetch the list of Nigerian banks from Paystack.
    Cache this in production — it rarely changes.
    """
    try:
        response = requests.get(
            PAYSTACK_BANKS_URL,
            headers=_get_headers(),
            params={'country': 'nigeria', 'per_page': 100},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        logger.error('Paystack get_banks timeout')
        return {'status': False, 'message': 'Could not load banks. Please try again.'}

    except Exception as e:
        logger.error(f'Paystack get_banks error: {e}')
        return {'status': False, 'message': 'Could not load banks.'}


# ============================================================
# OUTFLOW — Resolve Bank Account
#
# Before saving a vendor's bank account, we verify it exists.
# Paystack's resolve endpoint confirms the account number and
# bank code are valid and returns the registered account name.
#
# This prevents vendors from saving wrong account details
# and then complaining their withdrawal never arrived.
#
# Args:
#   account_number: 10-digit NUBAN account number
#   bank_code:      Paystack bank code (from get_banks())
#
# Returns a dict:
#   On success: {'status': True, 'data': {'account_name': 'JOHN DOE', ...}}
#   On failure: {'status': False, 'message': '...'}
# ============================================================
def resolve_bank_account(account_number, bank_code):
    """
    Verify a bank account exists and return the account holder's name.
    Always call this before saving a vendor's bank account details.
    """
    try:
        response = requests.get(
            PAYSTACK_RESOLVE_URL,
            headers=_get_headers(),
            params={
                'account_number': account_number,
                'bank_code': bank_code,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        logger.error(f'Paystack resolve timeout — account: {account_number}')
        return {'status': False, 'message': 'Account verification timeout. Please try again.'}

    except requests.exceptions.HTTPError:
        # 422 from Paystack means account not found
        return {'status': False, 'message': 'Account number not found. Please check your details.'}

    except Exception as e:
        logger.error(f'Paystack resolve error: {e}')
        return {'status': False, 'message': 'Could not verify account.'}


# ============================================================
# OUTFLOW — Create Transfer Recipient
#
# Before sending money to a vendor's bank, Paystack requires
# you to create a "Transfer Recipient" — a verified record of
# the destination bank account. This gives back a recipient_code
# which is then used in initiate_transfer().
#
# This is a one-time setup per bank account. Once created,
# store the recipient_code on the BankAccount model and reuse it.
#
# Args:
#   account_name:   Account holder name (from resolve_bank_account)
#   account_number: 10-digit NUBAN account number
#   bank_code:      Paystack bank code
#
# Returns a dict:
#   On success: {'status': True, 'data': {'recipient_code': 'RCP_...', ...}}
#   On failure: {'status': False, 'message': '...'}
# ============================================================
def create_transfer_recipient(account_name, account_number, bank_code):
    """
    Register a bank account as a Paystack Transfer Recipient.
    Store the returned recipient_code for future transfers.
    """
    payload = {
        'type': 'nuban',          # Nigerian Uniform Bank Account Number
        'name': account_name,
        'account_number': account_number,
        'bank_code': bank_code,
        'currency': 'NGN',
    }

    try:
        response = requests.post(
            PAYSTACK_RECIPIENT_URL,
            json=payload,
            headers=_get_headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        logger.error(f'Paystack create_recipient timeout — account: {account_number}')
        return {'status': False, 'message': 'Gateway timeout. Please try again.'}

    except requests.exceptions.HTTPError as e:
        logger.error(f'Paystack create_recipient HTTP error: {e}')
        return {'status': False, 'message': 'Could not register bank account. Please check your details.'}

    except Exception as e:
        logger.error(f'Paystack create_recipient error: {e}')
        return {'status': False, 'message': 'An unexpected error occurred.'}


# ============================================================
# OUTFLOW — Initiate Transfer (The Actual Payout)
#
# This is the function that sends real money to a vendor's bank.
# Only called after ALL withdrawal gates have passed:
#   ✓ Hold period expired
#   ✓ All audit checks passed
#   ✓ Admin approved (if above threshold)
#
# Args:
#   amount:          Amount in Naira (Decimal) to send
#   recipient_code:  The RCP_... code from create_transfer_recipient
#   reference:       Our withdrawal reference (for webhook matching)
#   reason:          Description shown on bank statement
#
# Returns a dict:
#   On success: {'status': True, 'data': {'transfer_code': 'TRF_...', ...}}
#   On failure: {'status': False, 'message': '...'}
# ============================================================
def initiate_transfer(amount, recipient_code, reference, reason='PayFusion Vendor Payout'):
    """
    Send money from PayFusion's Paystack balance to a vendor's bank.

    IMPORTANT: This debits PayFusion's Paystack account directly.
    Only call this after all withdrawal gates have been verified.
    The webhook (transfer.success / transfer.failed) will confirm
    the outcome and update balances accordingly.
    """
    payload = {
        'source': 'balance',        # Debit from our Paystack balance
        'amount': _to_kobo(amount),
        'recipient': recipient_code,
        'reason': reason,
        'reference': reference,
        'currency': 'NGN',
    }

    try:
        response = requests.post(
            PAYSTACK_TRANSFER_URL,
            json=payload,
            headers=_get_headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        logger.error(f'Paystack initiate_transfer timeout — reference: {reference}')
        return {'status': False, 'message': 'Transfer gateway timeout.'}

    except requests.exceptions.HTTPError as e:
        logger.error(f'Paystack initiate_transfer HTTP error: {e} — reference: {reference}')
        return {'status': False, 'message': 'Transfer initiation failed.'}

    except Exception as e:
        logger.error(f'Paystack initiate_transfer error: {e} — reference: {reference}')
        return {'status': False, 'message': 'An unexpected error occurred.'}