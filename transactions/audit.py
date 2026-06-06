import logging

from decimal import Decimal
from django.utils import timezone
from django.db import transaction

from core.models import PlatformConfig
from tenants.models import Wallet, WithdrawalRequest
from transactions.models import Transaction
from disputes.models import Dispute

logger = logging.getLogger('payfusion')


# ============================================================
# AUDIT RESULT HELPERS
# ============================================================
def _pass(notes, requires_admin_review=False):
    return {
        'passed': True,
        'requires_admin_review': requires_admin_review,
        'notes': notes,
    }


def _fail(reason):
    return {
        'passed': False,
        'requires_admin_review': False,
        'notes': reason,
    }


# ============================================================
# MAIN AUDIT FUNCTION
# ============================================================
def run_withdrawal_audit(withdrawal_request):
    """
    Run all automated audit checks on a withdrawal request.

    All policy thresholds are read from PlatformConfig.
    No hardcoded values anywhere in this function.

    Args:
        withdrawal_request: WithdrawalRequest instance to audit

    Returns:
        dict with keys:
            passed                (bool)
            requires_admin_review (bool)
            notes                 (str)
    """
    # Load all policy values from database
    # One call — no hardcoded numbers below this line
    config = PlatformConfig.get_config()

    auto_approval_threshold   = config.auto_approval_threshold
    fraud_failed_tx_threshold = config.fraud_failed_tx_threshold
    fraud_lookback_hours      = config.fraud_lookback_hours

    business     = withdrawal_request.business
    bank_account = withdrawal_request.bank_account
    amount       = withdrawal_request.amount

    audit_log = []
    failed = False
    failure_reason = None
    requires_admin_review = False

    logger.info(
        f'Audit started — business: {business.name} | '
        f'amount: N{amount:,.2f} | '
        f'withdrawal_request: #{withdrawal_request.id}'
    )

    # CHECK 1 — Bank account verified
    if not bank_account:
        failed = True
        failure_reason = 'No bank account linked to this withdrawal request.'
        audit_log.append(f'[FAIL] Check 1 — Bank account: {failure_reason}')
    elif not bank_account.is_verified:
        failed = True
        failure_reason = (
            f'Bank account {bank_account.bank_name} '
            f'****{bank_account.account_number[-4:]} '
            f'has not been verified by Paystack. '
            f'Please re-add your bank account.'
        )
        audit_log.append(f'[FAIL] Check 1 — Bank account verification: {failure_reason}')
    else:
        audit_log.append(
            f'[PASS] Check 1 — Bank account verified: '
            f'{bank_account.bank_name} ****{bank_account.account_number[-4:]}'
        )

    # CHECK 2 — Recipient code exists
    if not failed:
        if not bank_account.recipient_code:
            failed = True
            failure_reason = (
                'Bank account is not registered for transfers. '
                'Please remove and re-add your bank account.'
            )
            audit_log.append(f'[FAIL] Check 2 — Recipient code: {failure_reason}')
        else:
            audit_log.append(
                f'[PASS] Check 2 — Recipient code present: '
                f'{bank_account.recipient_code[:12]}...'
            )

    # CHECK 3 — No open disputes
    blocking_disputes = Dispute.objects.filter(
        business=business,
        status__in=('open', 'under_review'),
    )
    dispute_count = blocking_disputes.count()

    if dispute_count > 0:
        failed = True
        failure_reason = (
            f'You have {dispute_count} open dispute(s) on your account. '
            f'Withdrawals are blocked until all disputes are resolved. '
            f'Please contact support or check your disputes dashboard.'
        )
        audit_log.append(f'[FAIL] Check 3 — Open disputes: {failure_reason}')
    else:
        audit_log.append('[PASS] Check 3 — No open disputes found')

    # CHECK 4 — Transaction record valid
    if not failed:
        txn = withdrawal_request.transaction
        if not txn:
            failed = True
            failure_reason = (
                'No transaction record found for this withdrawal. '
                'This is a system error — please contact support.'
            )
            audit_log.append(f'[FAIL] Check 4 — Transaction record: {failure_reason}')
        elif txn.status != 'pending':
            failed = True
            failure_reason = (
                f'Withdrawal transaction is in unexpected state: {txn.status}. '
                f'Expected: pending. Please contact support.'
            )
            audit_log.append(f'[FAIL] Check 4 — Transaction state: {failure_reason}')
        else:
            audit_log.append(
                f'[PASS] Check 4 — Transaction record valid: '
                f'{txn.reference} | status: pending'
            )

    # CHECK 5 — Suspicious activity (always runs)
    lookback_time = timezone.now() - timezone.timedelta(hours=fraud_lookback_hours)
    recent_failures = Transaction.objects.filter(
        business=business,
        status='failed',
        created_at__gte=lookback_time,
    ).count()

    if recent_failures >= fraud_failed_tx_threshold:
        requires_admin_review = True
        audit_log.append(
            f'[FLAG] Check 5 — Suspicious activity: '
            f'{recent_failures} failed transactions in last {fraud_lookback_hours}h '
            f'(threshold: {fraud_failed_tx_threshold}). Flagged for admin review.'
        )
        logger.warning(
            f'Fraud flag — business: {business.name} | '
            f'failed transactions: {recent_failures} in {fraud_lookback_hours}h'
        )
    else:
        audit_log.append(
            f'[PASS] Check 5 — Activity check: '
            f'{recent_failures} failed transactions in last {fraud_lookback_hours}h '
            f'(threshold: {fraud_failed_tx_threshold})'
        )

    # CHECK 6 — Large amount threshold (always runs)
    if amount >= auto_approval_threshold:
        requires_admin_review = True
        audit_log.append(
            f'[FLAG] Check 6 — Large withdrawal: '
            f'N{amount:,.2f} meets or exceeds threshold '
            f'N{auto_approval_threshold:,.2f}. Flagged for manual admin review.'
        )
    else:
        audit_log.append(
            f'[PASS] Check 6 — Amount within auto-approval threshold: '
            f'N{amount:,.2f} < N{auto_approval_threshold:,.2f}'
        )

    # Compile result
    full_audit_notes = '\n'.join(audit_log)

    if failed:
        result = _fail(failure_reason)
        result['notes'] = full_audit_notes
    else:
        result = _pass(full_audit_notes, requires_admin_review)

    # Write audit result to WithdrawalRequest
    with transaction.atomic():
        wr = WithdrawalRequest.objects.select_for_update().get(
            id=withdrawal_request.id
        )

        wr.audit_passed = result['passed']
        wr.audit_notes = result['notes']
        wr.requires_admin_review = result.get('requires_admin_review', False)

        if not result['passed']:
            wr.status = 'audit_failed'
            wr.rejection_reason = failure_reason
            wr.save(update_fields=[
                'audit_passed', 'audit_notes', 'requires_admin_review',
                'status', 'rejection_reason', 'updated_at',
            ])

            # Reverse wallet hold
            from transactions.services import credit_wallet
            credit_wallet(
                business=business,
                amount=amount,
                description=(
                    f'Audit reversal: Withdrawal #{withdrawal_request.id} '
                    f'blocked — {failure_reason}'
                ),
            )

            logger.info(
                f'Audit FAILED — business: {business.name} | '
                f'amount: N{amount:,.2f} | reason: {failure_reason}'
            )

        else:
            now = timezone.now()
            hold_expires = wr.hold_expires_at

            if hold_expires and hold_expires > now:
                wr.status = 'pending_hold'
            elif wr.requires_admin_review:
                wr.status = 'pending_approval'
            else:
                wr.status = 'approved'

            wr.save(update_fields=[
                'audit_passed', 'audit_notes', 'requires_admin_review',
                'status', 'updated_at',
            ])

            logger.info(
                f'Audit PASSED — business: {business.name} | '
                f'amount: N{amount:,.2f} | '
                f'status: {wr.status} | '
                f'admin_review: {wr.requires_admin_review}'
            )

    return result