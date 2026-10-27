"""Staging-only request adapter for PostgreSQL finance primitives.

No HTTP routing or database connection is established here. This is an
explicitly called compatibility boundary, NOT a SQLite -> PG feature switch.
"""
from decimal import Decimal, InvalidOperation
from omata_pg_finance import create_review_draft, confirm_review_draft, MAIN_LOCATION_ID

WALK_IN_CUSTOMER_ID = 'walk-in-cash'
MANAGEMENT = {'owner', 'admin'}
SALES = MANAGEMENT | {'branch_manager', 'cashier'}


def _money(value):
    """Convert UI naira decimal into exact integer kobo, matching app.money."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Invalid money amount.') from None
    if not d.is_finite() or d < 0 or d > 1000000000 or d * 100 != (d * 100).to_integral_value():
        raise ValueError('Use a non-negative amount with at most two decimal places.')
    return int(d * 100)


def _actor(actor, merchant, kind):
    if not isinstance(actor, dict) or not actor.get('id') or not actor.get('merchant'):
        raise PermissionError('Authenticated actor required')
    if actor['merchant'] != merchant:
        raise PermissionError('Cross-merchant action refused')
    if actor.get('must_change_password'):
        raise PermissionError('Password change required')
    roles = MANAGEMENT if kind == 'repayment' else SALES
    if actor.get('role') not in roles:
        raise PermissionError('Actor role cannot perform this transaction')


def adapt_draft_request(data, actor):
    """Fail closed before reaching the PG finance module or a database."""
    if not isinstance(data, dict):
        raise ValueError('Expected a request object')
    required = ('merchant', 'source', 'kind', 'customer', 'description')
    x = {key: str(data.get(key) or '').strip() for key in required}
    if not all(x.values()) or any(len(x[k]) > 2000 for k in required):
        raise ValueError('Required request fields missing or too long')
    kind, merchant = x['kind'], x['merchant']
    if kind not in ('sale', 'repayment'):
        raise ValueError('Choose sale or repayment')
    _actor(actor, merchant, kind)
    total, paid = _money(data.get('total', '')), _money(data.get('paid', ''))
    if kind == 'sale' and (total <= 0 or paid > total):
        raise ValueError('Invalid sale amount')
    if kind == 'repayment' and (total != 0 or paid <= 0):
        raise ValueError('Invalid repayment amount')
    if x['customer'] == WALK_IN_CUSTOMER_ID:
        # PG finance module requires a registered customer; avoid fabricating one.
        raise ValueError('Walk-in customer is not supported by staging PostgreSQL finance')
    if str(data.get('product') or '').strip() or str(data.get('quantity') or '').strip():
        raise ValueError('Inventory-linked sales are not supported by staging PostgreSQL finance')
    if str(data.get('overpayment_action') or '').strip():
        raise ValueError('Customer-credit overpayment is not supported by staging PostgreSQL finance')
    location = str(data.get('location_id') or '').strip()
    if kind == 'sale' and not location:
        raise ValueError('Choose the sale location')
    if kind == 'repayment':
        location = location or MAIN_LOCATION_ID
    if actor['role'] not in MANAGEMENT:
        allowed = {str(v.get('id')) for v in (actor.get('locations') or []) if isinstance(v, dict) and v.get('id')}
        if location not in allowed:
            raise PermissionError('Actor not assigned to this location')
    return dict(actor=actor, merchant=merchant, source=x['source'], kind=kind,
                customer=x['customer'], description=x['description'],
                total=total, paid=paid, location_id=location)


def draft_with_existing_connection(db, data, actor):
    """Explicit staging entry point. Caller owns DB transaction and commit."""
    kwargs = adapt_draft_request(data, actor)
    return create_review_draft(db, **kwargs)


def confirm_with_existing_connection(db, entry_id, actor):
    """Explicit human test actor only; module re-checks location and merchant."""
    if not isinstance(actor, dict) or not actor.get('id') or not actor.get('merchant'):
        raise PermissionError('Authenticated actor required')
    if actor.get('must_change_password'):
        raise PermissionError('Password change required')
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ValueError('Entry ID required')
    return confirm_review_draft(db, actor=actor, merchant=actor['merchant'], entry_id=entry_id.strip())
