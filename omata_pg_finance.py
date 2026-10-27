"""Opt-in, STAGING-ONLY PostgreSQL finance draft/confirmation primitives.

Not imported by app.py or WhatsApp. No inventory-linked sales, customer credits,
receipts, or message sending. Amounts are integer minor units, matching entries.
"""
import json
import uuid
from omata_pg_staging import staging_connection, StagingGuardError

MAIN_LOCATION_ID = '__main__'
ALLOWED_ROLES = {'owner', 'admin', 'branch_manager', 'cashier'}
MANAGEMENT_ROLES = {'owner', 'admin'}


def _actor(actor, merchant, *, location_id, repayment=False):
    if not isinstance(actor, dict) or not actor.get('id') or not actor.get('role') or not actor.get('merchant'):
        raise PermissionError('Authenticated actor required')
    if actor['merchant'] != merchant:
        raise PermissionError('Cross-merchant action refused')
    if repayment:
        if actor['role'] not in MANAGEMENT_ROLES:
            raise PermissionError('Repayment requires owner/admin')
    elif actor['role'] not in ALLOWED_ROLES:
        raise PermissionError('Role cannot post sales')
    if actor['role'] not in MANAGEMENT_ROLES:
        location_ids = {str(i.get('id')) for i in actor.get('locations', []) if isinstance(i, dict)}
        if location_id not in location_ids:
            raise PermissionError('Actor not assigned to location')


def _audit(cur, actor, action, entry_id, kind):
    cur.execute('''INSERT INTO audit_log
        (id,merchant,user_id,role,action,target_type,target_id,details)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
        (str(uuid.uuid4()), actor['merchant'], actor['id'], actor['role'],
         action, 'entry', entry_id, json.dumps({'kind': kind, 'staging': True})))


def _require_location(cur, merchant, location_id):
    if location_id == MAIN_LOCATION_ID:
        return
    cur.execute('SELECT 1 FROM locations WHERE merchant=%s AND id=%s', (merchant, location_id))
    if cur.fetchone() is None:
        raise ValueError('Location does not belong to merchant')


def _money(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**63 - 1:
        raise ValueError('Amount must be nonnegative integer minor units')
    return value


def create_review_draft(db, *, actor, merchant, source, kind, customer, description,
                        total, paid, location_id, product=None, quantity=None):
    """Create staging draft only; never posts to ledger totals until confirmed.

    Excludes inventory-linked sales and customer-credit repayments pending full parity.
    Caller supplies explicit staged writable db and controls commit.
    """
    if product is not None or quantity is not None:
        raise ValueError('Inventory-linked drafts not supported by this phase')
    if kind not in ('sale', 'repayment'):
        raise ValueError('Invalid financial kind')
    if not all(isinstance(v, str) and v.strip() and len(v) <= 2000
               for v in (merchant, source, customer, description, location_id)):
        raise ValueError('Invalid required field')
    total, paid = _money(total), _money(paid)
    if (kind == 'sale' and (total <= 0 or paid > total)) or (kind == 'repayment' and (total != 0 or paid <= 0)):
        raise ValueError('Invalid sale or repayment amount')
    _actor(actor, merchant, location_id=location_id, repayment=(kind == 'repayment'))
    with db.cursor() as cur:
        # Serializes all financial operations for this merchant, including another
        # instance using these same primitives. Other legacy writers are NOT covered.
        cur.execute('SELECT 1 FROM shops WHERE id=%s FOR UPDATE', (merchant,))
        if cur.fetchone() is None:
            raise ValueError('Unknown merchant')
        _require_location(cur, merchant, location_id)
        cur.execute('SELECT 1 FROM customers WHERE merchant=%s AND id=%s', (merchant, customer))
        if cur.fetchone() is None:
            raise ValueError('Customer not registered')
        cur.execute('''SELECT id, kind, customer, description, total, paid, location_id
                        FROM entries WHERE merchant=%s AND source=%s''', (merchant, source))
        existing = cur.fetchone()
        expected = (kind, customer, description, total, paid, location_id)
        if existing:
            actual = tuple(existing[x] for x in ('kind', 'customer', 'description', 'total', 'paid', 'location_id'))
            if actual != expected:
                raise ValueError('Conflicting source ID')
            # Don't claim parity for existing inventory-linked or credited drafts.
            cur.execute('SELECT 1 FROM stock_moves WHERE entry_id=%s', (existing['id'],))
            if cur.fetchone() is not None:
                raise ValueError('Existing entry has inventory movement')
            return existing['id']
        if kind == 'repayment':
            cur.execute('''SELECT COALESCE(SUM(total-paid+COALESCE(customer_credit,0)),0) AS balance
                FROM entries WHERE merchant=%s AND customer=%s AND status='confirmed' ''', (merchant, customer))
            if paid > cur.fetchone()['balance']:
                raise ValueError('Repayment exceeds confirmed debt')
        ident = str(uuid.uuid4())
        cur.execute('''INSERT INTO entries
            (id,merchant,source,kind,customer,description,total,paid,status,customer_credit,location_id,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'draft',0,%s,%s)''',
            (ident, merchant, source, kind, customer, description, total, paid, location_id, actor['id']))
        _audit(cur, actor, 'entry.draft', ident, kind)
        return ident


def confirm_review_draft(db, *, actor, merchant, entry_id):
    """Requires human actor. Lock merchant, then entry before confirmation."""
    if not isinstance(merchant, str) or not merchant or not isinstance(entry_id, str) or not entry_id:
        raise ValueError('Missing merchant or entry ID')
    if not isinstance(actor, dict) or actor.get('merchant') != merchant:
        raise PermissionError('Cross-merchant action refused')
    with db.cursor() as cur:
        cur.execute('SELECT 1 FROM shops WHERE id=%s FOR UPDATE', (merchant,))
        if cur.fetchone() is None:
            raise ValueError('Unknown merchant')
        cur.execute('SELECT * FROM entries WHERE merchant=%s AND id=%s FOR UPDATE', (merchant, entry_id))
        row = cur.fetchone()
        if row is None:
            raise ValueError('Entry not found for merchant')
        _actor(actor, merchant, location_id=row['location_id'], repayment=row['kind'] == 'repayment')
        if row['status'] == 'confirmed':
            return False
        if row['status'] != 'draft':
            raise ValueError('Entry not an active draft')

        # OMATA_STEP_172E_CUSTOMER_REVALIDATION
        # Reject orphaned or cross-merchant customer references.
        cur.execute(
            'SELECT 1 FROM customers WHERE merchant=%s AND id=%s',
            (merchant, row['customer'])
        )
        if cur.fetchone() is None:
            raise ValueError(
                'Customer identity is no longer valid; review required'
            )

        if int(row['customer_credit'] or 0):
            raise ValueError('Customer-credit confirmation unsupported')
        cur.execute('SELECT 1 FROM stock_moves WHERE entry_id=%s', (entry_id,))
        if cur.fetchone() is not None:
            raise ValueError('Inventory-linked confirmation unsupported')
        if row['kind'] == 'repayment':
            cur.execute('''SELECT COALESCE(SUM(total-paid+COALESCE(customer_credit,0)),0) AS balance
                FROM entries WHERE merchant=%s AND customer=%s AND status='confirmed' ''',
                (merchant, row['customer']))
            if row['paid'] > cur.fetchone()['balance']:
                raise ValueError('Debt changed; review repayment again')
        cur.execute('''UPDATE entries SET status='confirmed', confirmed=CURRENT_TIMESTAMP,
            confirmed_by=%s WHERE merchant=%s AND id=%s AND status='draft' ''',
            (actor['id'], merchant, entry_id))
        if cur.rowcount != 1:
            raise RuntimeError('Concurrent confirmation conflict')
        _audit(cur, actor, 'entry.confirm', entry_id, row['kind'])
        return True


__all__ = ['create_review_draft', 'confirm_review_draft', 'staging_connection']
