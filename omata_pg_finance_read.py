"""Read-only, merchant-scoped financial projection for isolated PostgreSQL development.

Not a replacement for app.state(): inventory/payment/customer-credit views remain unported.
Caller must supply a PostgreSQL connection and authenticated actor from trusted session code.
"""

MANAGEMENT = {'owner', 'admin'}
BRANCH = {'branch_manager', 'cashier', 'inventory_officer'}
ROLES = MANAGEMENT | BRANCH


def finance_snapshot(db, *, actor):
    if not isinstance(actor, dict) or not actor.get('id') or not actor.get('merchant') or actor.get('role') not in ROLES:
        raise PermissionError('Authenticated finance actor required')
    merchant = actor['merchant']
    if not isinstance(merchant, str) or not merchant.strip():
        raise PermissionError('Valid merchant required')
    allowed = None
    if actor['role'] in BRANCH:
        allowed = {str(x['id']) for x in actor.get('locations', []) if isinstance(x, dict) and x.get('id')}
        if not allowed:
            # Deny rather than revealing customer totals when branch assignment is absent.
            return {'entries': [], 'balances': [], 'summary': [], 'scope': 'assigned_locations_only'}
    with db.cursor() as cur:
        if allowed is None:
            cur.execute('''SELECT id,source,kind,customer,description,total,paid,status,location_id,created,confirmed
                FROM entries WHERE merchant=%s ORDER BY created DESC,id DESC LIMIT 500''', (merchant,))
        else:
            cur.execute('''SELECT id,source,kind,customer,description,total,paid,status,location_id,created,confirmed
                FROM entries WHERE merchant=%s AND location_id = ANY(%s) ORDER BY created DESC,id DESC LIMIT 500''',
                (merchant, list(allowed)))
        entries = [dict(r) for r in cur.fetchall()]
        if allowed is not None:
            # Keep customer-wide balances hidden for branch-scoped users; their totals can span other branches.
            return {'entries': entries, 'balances': [], 'summary': [], 'scope': 'assigned_locations_only'}
        cur.execute('''SELECT customer, COALESCE(SUM(total-paid+COALESCE(customer_credit,0)),0) AS balance
            FROM entries WHERE merchant=%s AND status='confirmed' GROUP BY customer ORDER BY customer''', (merchant,))
        balances = [dict(r) for r in cur.fetchall()]
        cur.execute('''SELECT COALESCE(SUM(total),0) AS sales,
            COALESCE(SUM(paid),0) AS collections,
            COALESCE(SUM(total-paid+COALESCE(customer_credit,0)),0) AS debt
            FROM entries WHERE merchant=%s AND status='confirmed' ''', (merchant,))
        summary = dict(cur.fetchone())
        return {'entries': entries, 'balances': balances, 'summary': [summary], 'scope': 'merchant'}
