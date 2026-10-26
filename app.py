"""Local pilot only: reviewed entries, SQLite ledger, optional inbound WhatsApp review inbox."""



import os



from urllib.parse import urlparse, parse_qs



import voice



import json, sqlite3, uuid, os, secrets, threading, hmac, hashlib, re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError



from pathlib import Path



from http.server import BaseHTTPRequestHandler, HTTPServer







DB = os.environ.get('LEDGER_DB', str(Path(__file__).with_name('ledger.sqlite3')))



TOKEN = secrets.token_urlsafe(32)







WHATSAPP_VERIFY_TOKEN = os.environ.get(



    'WHATSAPP_VERIFY_TOKEN',



    'voice-ledger-whatsapp-verify'



)



WHATSAPP_DEFAULT_MERCHANT = os.environ.get(



    'WHATSAPP_DEFAULT_MERCHANT',



    'Ezebo Plaza Shop'



)

WHATSAPP_ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN', '').strip()
WHATSAPP_GRAPH_VERSION = os.environ.get('WHATSAPP_GRAPH_VERSION', 'v26.0').strip() or 'v26.0'
WHATSAPP_APP_SECRET = os.environ.get('WHATSAPP_APP_SECRET', '').strip()
WHATSAPP_MEDIA_MAX_BYTES = int(os.environ.get('WHATSAPP_MEDIA_MAX_BYTES', str(getattr(voice, 'MAX_AUDIO_BYTES', 10 * 1024 * 1024))))

# Reserved system customer for anonymous, fully paid counter sales.
WALK_IN_CUSTOMER_ID = 'walk-in-cash'
WALK_IN_CUSTOMER_NAME = 'Walk-in / Cash Customer'

AUTH_SESSION_HOURS = int(os.environ.get('LEDGER_SESSION_HOURS', '12'))
AUTH_PBKDF2_ROUNDS = int(os.environ.get('LEDGER_PBKDF2_ROUNDS', '310000'))
AUTH_ROLES = ('owner','admin','branch_manager','cashier','inventory_officer','auditor')
AUTH_MANAGE_ROLES = ('owner','admin')
MUTATION_ROLES = ('owner','admin')  # Legacy coarse gate; branch mutations use fine-grained controls.

ROLE_CAPABILITIES = {
    'owner': ['company_view','manage_users','post_sales','confirm_sales','receive_stock','adjust_stock','transfer_stock','reverse_confirmed','view_reports','backup'],
    'admin': ['company_view','manage_users','post_sales','confirm_sales','receive_stock','adjust_stock','transfer_stock','reverse_confirmed','view_reports','backup'],
    'branch_manager': ['branch_view','post_sales','confirm_sales','receive_stock','adjust_stock','transfer_stock','view_branch_reports'],
    'cashier': ['branch_view','post_sales','confirm_own_sales','view_branch_reports'],
    'inventory_officer': ['branch_view','receive_stock','adjust_stock','transfer_stock','view_branch_reports'],
    'auditor': ['read_only','view_reports'],
}








def connect():



    c = sqlite3.connect(DB)



    c.row_factory = sqlite3.Row



    c.executescript('''CREATE TABLE IF NOT EXISTS entries (



      id TEXT PRIMARY KEY, merchant TEXT NOT NULL, source TEXT NOT NULL,



      kind TEXT NOT NULL, customer TEXT NOT NULL, description TEXT NOT NULL,



      total INTEGER NOT NULL, paid INTEGER NOT NULL, status TEXT NOT NULL,



      created TEXT DEFAULT CURRENT_TIMESTAMP, confirmed TEXT,



      UNIQUE(merchant, source));''')

    entry_columns={r['name'] for r in c.execute('PRAGMA table_info(entries)')}
    if 'customer_credit' not in entry_columns:
        c.execute('ALTER TABLE entries ADD COLUMN customer_credit INTEGER NOT NULL DEFAULT 0')
    if 'location_id' not in entry_columns:
        c.execute('ALTER TABLE entries ADD COLUMN location_id TEXT')
    if 'created_by' not in entry_columns:
        c.execute('ALTER TABLE entries ADD COLUMN created_by TEXT')
    if 'confirmed_by' not in entry_columns:
        c.execute('ALTER TABLE entries ADD COLUMN confirmed_by TEXT')



    if 'expected_unit_price' not in entry_columns:
        c.execute('ALTER TABLE entries ADD COLUMN expected_unit_price INTEGER')
    if 'expected_total' not in entry_columns:
        c.execute('ALTER TABLE entries ADD COLUMN expected_total INTEGER')
    if 'price_variance' not in entry_columns:
        c.execute('ALTER TABLE entries ADD COLUMN price_variance INTEGER')
    if 'pricing_source' not in entry_columns:
        c.execute('ALTER TABLE entries ADD COLUMN pricing_source TEXT')

    c.executescript("""CREATE TABLE IF NOT EXISTS customers (



      merchant TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,



      PRIMARY KEY(merchant,id));



      CREATE TABLE IF NOT EXISTS reversals (



      entry_id TEXT PRIMARY KEY, reason TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);



      INSERT OR IGNORE INTO customers(merchant,id,name)



      SELECT DISTINCT merchant,customer,customer FROM entries;""")



    c.executescript("""CREATE TABLE IF NOT EXISTS payment_records (
      id TEXT PRIMARY KEY,
      merchant TEXT NOT NULL,
      entry_id TEXT,
      location_id TEXT NOT NULL,
      method TEXT NOT NULL,
      amount INTEGER NOT NULL,
      institution TEXT,
      reference TEXT,
      payment_date TEXT,
      status TEXT NOT NULL DEFAULT 'presented',
      submitted_by TEXT,
      created TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(merchant,reference)
    );

    CREATE TABLE IF NOT EXISTS reconciliation_sessions (
      id TEXT PRIMARY KEY,
      merchant TEXT NOT NULL,
      location_id TEXT,
      period_start TEXT NOT NULL,
      period_end TEXT NOT NULL,
      expected_collections INTEGER NOT NULL DEFAULT 0,
      presented_collections INTEGER NOT NULL DEFAULT 0,
      matched_amount INTEGER NOT NULL DEFAULT 0,
      variance INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'open',
      created_by TEXT,
      approved_by TEXT,
      created TEXT DEFAULT CURRENT_TIMESTAMP,
      approved TEXT
    );

    CREATE TABLE IF NOT EXISTS reconciliation_matches (
      id TEXT PRIMARY KEY,
      merchant TEXT NOT NULL,
      session_id TEXT NOT NULL,
      payment_id TEXT NOT NULL,
      entry_id TEXT NOT NULL,
      matched_amount INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'matched',
      created_by TEXT,
      created TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(session_id,payment_id,entry_id)
    );

    CREATE INDEX IF NOT EXISTS idx_payment_records_merchant_entry
      ON payment_records(merchant,entry_id);

    CREATE INDEX IF NOT EXISTS idx_payment_records_merchant_location
      ON payment_records(merchant,location_id);

    CREATE INDEX IF NOT EXISTS idx_recon_sessions_merchant_period
      ON reconciliation_sessions(merchant,period_start,period_end);

    CREATE INDEX IF NOT EXISTS idx_recon_matches_session
      ON reconciliation_matches(session_id);

    CREATE TABLE IF NOT EXISTS daily_closings (
      id TEXT PRIMARY KEY,
      merchant TEXT NOT NULL,
      location_id TEXT NOT NULL,
      business_date TEXT NOT NULL,
      reconciliation_session_id TEXT NOT NULL,
      expected_collections INTEGER NOT NULL DEFAULT 0,
      presented_collections INTEGER NOT NULL DEFAULT 0,
      matched_amount INTEGER NOT NULL DEFAULT 0,
      variance INTEGER NOT NULL DEFAULT 0,
      closing_status TEXT NOT NULL DEFAULT 'submitted',
      closing_note TEXT,
      submitted_by TEXT,
      submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
      approved_by TEXT,
      approved_at TEXT,
      UNIQUE(merchant,location_id,business_date)
    );

    CREATE INDEX IF NOT EXISTS idx_daily_closings_merchant_date
      ON daily_closings(merchant,business_date);

    CREATE INDEX IF NOT EXISTS idx_daily_closings_location_date
      ON daily_closings(merchant,location_id,business_date);
    """)

    recon_session_columns={
        r[1] for r in c.execute("PRAGMA table_info(reconciliation_sessions)")
    }
    if 'exception_reason' not in recon_session_columns:
        c.execute(
            "ALTER TABLE reconciliation_sessions "
            "ADD COLUMN exception_reason TEXT"
        )

    c.executescript("""CREATE TABLE IF NOT EXISTS products (



      merchant TEXT NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,unit TEXT NOT NULL,



      PRIMARY KEY(merchant,id));



      CREATE TABLE IF NOT EXISTS stock_moves (



      id TEXT PRIMARY KEY,merchant TEXT NOT NULL,source TEXT NOT NULL,



      product TEXT NOT NULL,quantity INTEGER NOT NULL,kind TEXT NOT NULL,



      status TEXT NOT NULL,entry_id TEXT UNIQUE,reason TEXT,



      created TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(merchant,source));""")



    product_columns={r['name'] for r in c.execute('PRAGMA table_info(products)')}
    if 'base_selling_price' not in product_columns:
        c.execute('ALTER TABLE products ADD COLUMN base_selling_price INTEGER')

    c.executescript("""CREATE TABLE IF NOT EXISTS branch_prices (
      merchant TEXT NOT NULL,
      product TEXT NOT NULL,
      location_id TEXT NOT NULL,
      logistics INTEGER NOT NULL DEFAULT 0,
      handling INTEGER NOT NULL DEFAULT 0,
      miscellaneous INTEGER NOT NULL DEFAULT 0,
      adjustment INTEGER NOT NULL DEFAULT 0,
      final_price INTEGER NOT NULL,
      min_price INTEGER,
      max_price INTEGER,
      reason TEXT,
      updated_by TEXT,
      updated TEXT DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(merchant,product,location_id)
    );

    CREATE TABLE IF NOT EXISTS branch_price_history (
      id TEXT PRIMARY KEY,
      merchant TEXT NOT NULL,
      product TEXT NOT NULL,
      location_id TEXT NOT NULL,
      base_price INTEGER,
      logistics INTEGER NOT NULL DEFAULT 0,
      handling INTEGER NOT NULL DEFAULT 0,
      miscellaneous INTEGER NOT NULL DEFAULT 0,
      adjustment INTEGER NOT NULL DEFAULT 0,
      final_price INTEGER NOT NULL,
      min_price INTEGER,
      max_price INTEGER,
      reason TEXT,
      changed_by TEXT,
      created TEXT DEFAULT CURRENT_TIMESTAMP
    );""")

    stock_columns={r['name'] for r in c.execute('PRAGMA table_info(stock_moves)')}

    if 'cost_total' not in stock_columns:
        c.execute('ALTER TABLE stock_moves ADD COLUMN cost_total INTEGER')
    if 'supplier' not in stock_columns:
        c.execute('ALTER TABLE stock_moves ADD COLUMN supplier TEXT')
    if 'destination' not in stock_columns:
        c.execute('ALTER TABLE stock_moves ADD COLUMN destination TEXT')
    if 'counted_quantity' not in stock_columns:
        c.execute('ALTER TABLE stock_moves ADD COLUMN counted_quantity INTEGER')
    if 'baseline_quantity' not in stock_columns:
        c.execute('ALTER TABLE stock_moves ADD COLUMN baseline_quantity INTEGER')
    if 'location_id' not in stock_columns:
        c.execute('ALTER TABLE stock_moves ADD COLUMN location_id TEXT')

    c.executescript("""CREATE TABLE IF NOT EXISTS suppliers (
      merchant TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
      PRIMARY KEY(merchant,id));""")

    c.executescript("""CREATE TABLE IF NOT EXISTS locations (
      merchant TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
      PRIMARY KEY(merchant,id));""")

    c.executescript("""CREATE TABLE IF NOT EXISTS stock_transfers (
      id TEXT PRIMARY KEY,
      merchant TEXT NOT NULL,
      source TEXT NOT NULL,
      product TEXT NOT NULL,
      quantity INTEGER NOT NULL,
      from_location TEXT NOT NULL,
      to_location TEXT NOT NULL,
      status TEXT NOT NULL,
      cost_total INTEGER,
      reason TEXT,
      created TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(merchant,source)
    );""")



    c.executescript("""CREATE TABLE IF NOT EXISTS shops(id TEXT PRIMARY KEY,name TEXT NOT NULL);



      INSERT OR IGNORE INTO shops SELECT merchant,merchant FROM entries;



      INSERT OR IGNORE INTO shops SELECT merchant,merchant FROM customers;



      INSERT OR IGNORE INTO shops SELECT merchant,merchant FROM products;""")

    c.executescript("""CREATE TABLE IF NOT EXISTS users (
      merchant TEXT NOT NULL,
      id TEXT NOT NULL,
      name TEXT NOT NULL,
      username TEXT NOT NULL COLLATE NOCASE UNIQUE,
      password_salt TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      must_change_password INTEGER NOT NULL DEFAULT 0,
      created TEXT DEFAULT CURRENT_TIMESTAMP,
      updated TEXT DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(merchant,id)
    );

    CREATE TABLE IF NOT EXISTS user_locations (
      merchant TEXT NOT NULL,
      user_id TEXT NOT NULL,
      location_id TEXT NOT NULL,
      PRIMARY KEY(merchant,user_id,location_id)
    );

    CREATE TABLE IF NOT EXISTS auth_sessions (
      token_hash TEXT PRIMARY KEY,
      merchant TEXT NOT NULL,
      user_id TEXT NOT NULL,
      expires TEXT NOT NULL,
      created TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS audit_log (
      id TEXT PRIMARY KEY,
      merchant TEXT NOT NULL,
      user_id TEXT NOT NULL,
      role TEXT NOT NULL,
      action TEXT NOT NULL,
      target_type TEXT,
      target_id TEXT,
      details TEXT,
      created TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS auth_events (
      id TEXT PRIMARY KEY,
      merchant TEXT,
      username TEXT,
      user_id TEXT,
      action TEXT NOT NULL,
      details TEXT,
      created TEXT DEFAULT CURRENT_TIMESTAMP
    );""")

    user_columns={r['name'] for r in c.execute('PRAGMA table_info(users)')}
    if 'must_change_password' not in user_columns:
        c.execute('ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0')
    if 'updated' not in user_columns:
        # SQLite does not allow ALTER TABLE ADD COLUMN with a non-constant default.
        c.execute('ALTER TABLE users ADD COLUMN updated TEXT')
        c.execute('UPDATE users SET updated=COALESCE(updated,created,CURRENT_TIMESTAMP)')




    c.executescript("""CREATE TABLE IF NOT EXISTS inbox (



      id TEXT PRIMARY KEY, merchant TEXT NOT NULL, provider_id TEXT NOT NULL UNIQUE,



      sender TEXT NOT NULL, receiver TEXT NOT NULL, body TEXT NOT NULL,



      channel TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);



      CREATE TABLE IF NOT EXISTS inbox_claims (



      inbox_id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT NOT NULL);""")

    c.executescript("""CREATE TABLE IF NOT EXISTS voice_jobs (
      id TEXT PRIMARY KEY, merchant TEXT NOT NULL, provider_id TEXT NOT NULL UNIQUE,
      sender TEXT NOT NULL, receiver TEXT NOT NULL, media_id TEXT NOT NULL,
      mime_type TEXT, status TEXT NOT NULL, transcript TEXT, inbox_id TEXT, error TEXT,
      created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);""")



    return c







def money(v):



    from decimal import Decimal, InvalidOperation



    try:



        d = Decimal(str(v))



        if not d.is_finite() or d < 0 or d > 1000000000 or d*100 != (d*100).to_integral_value():



            raise ValueError('Use a non-negative amount with at most two decimal places.')



        return int(d*100)



    except InvalidOperation:



        raise ValueError('Invalid money amount.')







def _price_money(value, allow_blank=False):
    if allow_blank and (value is None or str(value).strip()==''):
        return None
    return money(value)

def _pricing_row(c,merchant,product,location_id):
    base=c.execute(
        'SELECT base_selling_price FROM products WHERE merchant=? AND id=?',
        (merchant,product)
    ).fetchone()
    base_price=base['base_selling_price'] if base else None
    branch=c.execute(
        'SELECT * FROM branch_prices WHERE merchant=? AND product=? AND location_id=?',
        (merchant,product,location_id)
    ).fetchone()
    if branch:
        return {
            'source':'branch',
            'base_price':base_price,
            'final_price':branch['final_price'],
            'min_price':branch['min_price'],
            'max_price':branch['max_price'],
            'logistics':branch['logistics'],
            'handling':branch['handling'],
            'miscellaneous':branch['miscellaneous'],
            'adjustment':branch['adjustment'],
            'reason':branch['reason'],
        }
    if base_price is not None:
        return {
            'source':'company',
            'base_price':base_price,
            'final_price':base_price,
            'min_price':base_price,
            'max_price':base_price,
            'logistics':0,'handling':0,'miscellaneous':0,'adjustment':0,
            'reason':None,
        }
    return None

def set_product_base_price(actor,data):
    if not actor or actor['role'] not in AUTH_MANAGE_ROLES:
        raise PermissionError('Only an owner or administrator can set the company/base selling price.')
    merchant=actor['merchant']
    product=str(data.get('product','')).strip()
    base=_price_money(data.get('base_price'))
    if base<=0:
        raise ValueError('Base selling price must be greater than zero.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        require_product(c,merchant,product)
        c.execute('UPDATE products SET base_selling_price=? WHERE merchant=? AND id=?',(base,merchant,product))
        audit(c,actor,'pricing.base_price','product',product,{'base_price':base})
    return {'ok':True}

def set_branch_price(actor,data):
    from decimal import Decimal, ROUND_HALF_UP
    if not actor:
        raise PermissionError('Authentication required.')
    if actor['role'] not in ('owner','admin','branch_manager'):
        raise PermissionError('Your role cannot change branch selling prices.')
    merchant=actor['merchant']
    product=str(data.get('product','')).strip()
    location_id=str(data.get('location_id','')).strip()
    reason=' '.join(str(data.get('reason','')).strip().split())
    if not reason:
        raise ValueError('Enter a reason for this branch price change.')

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        require_product(c,merchant,product)
        require_location(c,merchant,location_id)
        if actor['role']=='branch_manager' and not actor_can_use_location(actor,location_id):
            raise PermissionError('You can change pricing only for a branch assigned to your account.')

        product_row=c.execute(
            'SELECT base_selling_price FROM products WHERE merchant=? AND id=?',(merchant,product)
        ).fetchone()
        base=product_row['base_selling_price']
        if base is None or base<=0:
            raise ValueError('Set the company/base selling price before creating a branch price.')

        logistics=_price_money(data.get('logistics',0))
        handling=_price_money(data.get('handling',0))
        miscellaneous=_price_money(data.get('miscellaneous',0))
        adjustment=_price_money(data.get('adjustment',0))
        if min(logistics,handling,miscellaneous) < 0:
            raise ValueError('Logistics, handling and miscellaneous amounts cannot be negative.')

        calculated=base+logistics+handling+miscellaneous+adjustment
        if calculated<=0:
            raise ValueError('Calculated branch selling price must be greater than zero.')

        current=c.execute(
            'SELECT * FROM branch_prices WHERE merchant=? AND product=? AND location_id=?',
            (merchant,product,location_id)
        ).fetchone()

        if actor['role'] in AUTH_MANAGE_ROLES:
            min_price=_price_money(data.get('min_price'),allow_blank=True)
            max_price=_price_money(data.get('max_price'),allow_blank=True)
            if min_price is None:
                min_price=int((Decimal(base)*Decimal('0.95')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            if max_price is None:
                max_price=int((Decimal(base)*Decimal('1.15')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            if min_price<=0 or max_price<=0 or min_price>max_price:
                raise ValueError('Price guardrails are invalid.')
        else:
            if current:
                min_price=current['min_price']
                max_price=current['max_price']
            else:
                min_price=int((Decimal(base)*Decimal('0.95')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
                max_price=int((Decimal(base)*Decimal('1.15')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

        if actor['role']=='branch_manager' and (calculated<min_price or calculated>max_price):
            raise PermissionError(
                f'Calculated branch price is outside the allowed range: ₦{min_price/100:,.2f} to ₦{max_price/100:,.2f}.'
            )

        c.execute(
            'INSERT INTO branch_prices(merchant,product,location_id,logistics,handling,miscellaneous,adjustment,final_price,min_price,max_price,reason,updated_by,updated) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) '
            'ON CONFLICT(merchant,product,location_id) DO UPDATE SET '
            'logistics=excluded.logistics,handling=excluded.handling,miscellaneous=excluded.miscellaneous,'
            'adjustment=excluded.adjustment,final_price=excluded.final_price,min_price=excluded.min_price,'
            'max_price=excluded.max_price,reason=excluded.reason,updated_by=excluded.updated_by,updated=CURRENT_TIMESTAMP',
            (merchant,product,location_id,logistics,handling,miscellaneous,adjustment,calculated,min_price,max_price,reason,actor['id'])
        )
        c.execute(
            'INSERT INTO branch_price_history(id,merchant,product,location_id,base_price,logistics,handling,miscellaneous,adjustment,final_price,min_price,max_price,reason,changed_by) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (str(uuid.uuid4()),merchant,product,location_id,base,logistics,handling,miscellaneous,adjustment,calculated,min_price,max_price,reason,actor['id'])
        )
        audit(c,actor,'pricing.branch_price','branch_price',f'{product}:{location_id}',{
            'base_price':base,'logistics':logistics,'handling':handling,
            'miscellaneous':miscellaneous,'adjustment':adjustment,'final_price':calculated,
            'min_price':min_price,'max_price':max_price,'reason':reason
        })
        return {
            'base_price':base,'final_price':calculated,'min_price':min_price,'max_price':max_price
        }

def _sale_price_context(c,actor,merchant,product,location_id,qty,total):
    from decimal import Decimal, ROUND_HALF_UP
    pricing=_pricing_row(c,merchant,product,location_id)
    if not pricing or not qty:
        return None
    expected_total=int(
        (Decimal(pricing['final_price'])*Decimal(qty)/Decimal(1000)).quantize(
            Decimal('1'),rounding=ROUND_HALF_UP
        )
    )
    unit_actual=int(
        (Decimal(total)*Decimal(1000)/Decimal(qty)).quantize(
            Decimal('1'),rounding=ROUND_HALF_UP
        )
    )
    if actor:
        if actor['role']=='cashier' and total!=expected_total:
            raise PermissionError(
                f'Cashier sales must use the configured price. Expected total is ₦{expected_total/100:,.2f}.'
            )
        if actor['role']=='branch_manager':
            min_total=int(
                (Decimal(pricing['min_price'] or pricing['final_price'])*Decimal(qty)/Decimal(1000)).quantize(
                    Decimal('1'),rounding=ROUND_HALF_UP
                )
            )
            max_total=int(
                (Decimal(pricing['max_price'] or pricing['final_price'])*Decimal(qty)/Decimal(1000)).quantize(
                    Decimal('1'),rounding=ROUND_HALF_UP
                )
            )
            if total<min_total or total>max_total:
                raise PermissionError(
                    f'Sale price is outside the permitted branch range. Allowed total: ₦{min_total/100:,.2f} to ₦{max_total/100:,.2f}.'
                )
    return {
        'expected_unit_price':pricing['final_price'],
        'expected_total':expected_total,
        'price_variance':total-expected_total,
        'pricing_source':pricing['source'],
        'actual_unit_price':unit_actual
    }



def draft(data, actor=None):
    fields=['merchant','source','kind','customer','description']
    x={k:str(data.get(k,'')).strip() for k in fields}
    if not all(x.values()) or any(len(v)>2000 for v in x.values()):
        raise ValueError('All fields are required; maximum 2000 characters each.')
    if x['kind'] not in ('sale','repayment'):
        raise ValueError('Choose sale or repayment.')

    total,paid=money(data.get('total','')),money(data.get('paid',''))
    if x['kind']=='sale' and (total<=0 or paid>total):
        raise ValueError('Sale total must be positive and paid cannot exceed total.')
    if x['kind']=='repayment' and (total!=0 or paid<=0):
        raise ValueError('Repayment requires total 0 and a positive payment.')

    location_id=str(data.get('location_id','')).strip()
    if x['kind']=='sale':
        if not location_id:
            raise ValueError('Choose the location where this sale occurred.')
        require_sale_actor(actor,location_id)
    else:
        if actor and actor['role'] not in AUTH_MANAGE_ROLES:
            raise PermissionError('Branch repayment posting is not enabled in M6 Phase 1 yet.')
        location_id=location_id or MAIN_LOCATION_ID

    if x['customer']==WALK_IN_CUSTOMER_ID:
        if x['kind']!='sale':
            raise ValueError('Walk-in / Cash Customer can only be used for a sale.')
        if paid!=total:
            raise ValueError('Walk-in / Cash Customer is for fully paid sales only. Choose or create a named customer for credit or partial payment.')

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        check_inbox_source(c,x['merchant'],x['source'],'entry')
        require_location(c,x['merchant'],location_id)

        if x['customer']!=WALK_IN_CUSTOMER_ID:
            if not c.execute('SELECT 1 FROM customers WHERE merchant=? AND id=?',(x['merchant'],x['customer'])).fetchone():
                raise ValueError('Choose an existing customer or create the reviewed new customer first.')

        customer_credit=0
        if x['kind']=='repayment':
            balance=customer_balance(c,x['merchant'],x['customer'])
            if balance<=0:
                raise ValueError('This customer has no confirmed outstanding debt.')
            if paid>balance:
                if str(data.get('overpayment_action','')).strip()!='credit':
                    raise ValueError('Payment exceeds confirmed debt. Choose the customer-credit option or correct the amount.')
                customer_credit=paid-balance

        product_id=str(data.get('product','')).strip()
        qty=quantity(data.get('quantity')) if product_id else 0
        if not product_id and str(data.get('quantity','')).strip():
            raise ValueError('Choose a product for this quantity.')
        price_ctx=None
        if product_id:
            if x['kind']!='sale':
                raise ValueError('Only sales can deduct inventory.')
            require_product(c,x['merchant'],product_id)
            available=location_transfer_balance(c,x['merchant'],product_id,location_id)
            if available<qty:
                loc=require_location(c,x['merchant'],location_id)
                raise ValueError(f"Insufficient stock at {loc['name']}. Available: {available/1000:g}.")
            price_ctx=_sale_price_context(c,actor,x['merchant'],product_id,location_id,qty,total)

        existing=c.execute('SELECT * FROM entries WHERE merchant=? AND source=?',(x['merchant'],x['source'])).fetchone()
        if existing:
            mismatch=(any(existing[k]!=x[k] for k in fields) or existing['total']!=total or
                      existing['paid']!=paid or int(existing['customer_credit'] or 0)!=customer_credit or
                      (existing['location_id'] or MAIN_LOCATION_ID)!=location_id)
            if mismatch:
                raise ValueError('Source ID already exists with different data. Use a new source ID.')
            movement=c.execute('SELECT * FROM stock_moves WHERE entry_id=?',(existing['id'],)).fetchone()
            if (movement['product'] if movement else '')!=product_id or (movement['quantity'] if movement else 0)!=-qty:
                raise ValueError('Source ID already exists with different inventory data.')
            return existing['id']

        if x['customer']==WALK_IN_CUSTOMER_ID:
            c.execute('INSERT OR IGNORE INTO customers VALUES(?,?,?)',(x['merchant'],WALK_IN_CUSTOMER_ID,WALK_IN_CUSTOMER_NAME))

        ident=str(uuid.uuid4())
        created_by=actor['id'] if actor else None
        c.execute(
            'INSERT INTO entries(id,merchant,source,kind,customer,description,total,paid,status,customer_credit,location_id,created_by,expected_unit_price,expected_total,price_variance,pricing_source) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (
                ident,x['merchant'],x['source'],x['kind'],x['customer'],x['description'],total,paid,'draft',
                customer_credit,location_id,created_by,
                price_ctx['expected_unit_price'] if price_ctx else None,
                price_ctx['expected_total'] if price_ctx else None,
                price_ctx['price_variance'] if price_ctx else None,
                price_ctx['pricing_source'] if price_ctx else None
            )
        )
        if product_id:
            c.execute(
                'INSERT INTO stock_moves(id,merchant,source,product,quantity,kind,status,entry_id,location_id) VALUES(?,?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()),x['merchant'],'sale:'+ident,product_id,-qty,'sale','draft',ident,location_id)
            )
        claim_inbox(c,x['source'],'entry',ident)
        audit(c,actor,'entry.draft','entry',ident,{
            'kind':x['kind'],'location_id':location_id,'product':product_id or None,'quantity':qty or None,
            'expected_total':price_ctx['expected_total'] if price_ctx else None,
            'price_variance':price_ctx['price_variance'] if price_ctx else None,
            'pricing_source':price_ctx['pricing_source'] if price_ctx else None
        })
        return ident



def confirm(ident, actor=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM entries WHERE id=?',(ident,)).fetchone()
        if row is None:
            raise ValueError('Entry not found.')
        if row['status']=='confirmed':
            return
        if row['status']!='draft':
            raise ValueError('Only active drafts can be confirmed.')

        location_id=row['location_id'] or MAIN_LOCATION_ID
        if row['kind']=='sale':
            require_sale_actor(actor,location_id)
        elif actor and actor['role'] not in AUTH_MANAGE_ROLES:
            raise PermissionError('Branch repayment confirmation is not enabled in M6 Phase 1 yet.')

        if row['kind']=='repayment':
            balance=customer_balance(c,row['merchant'],row['customer'])
            debt_applied=row['paid']-int(row['customer_credit'] or 0)
            if debt_applied<=0:
                raise ValueError('Repayment must apply a positive amount to confirmed debt.')
            if debt_applied>balance:
                raise ValueError('Customer debt changed after this draft was created. Cancel the draft and review the repayment again.')

        movement=c.execute('SELECT * FROM stock_moves WHERE entry_id=?',(ident,)).fetchone()
        if movement:
            move_location=movement['location_id'] or location_id
            current_stock=location_transfer_balance(c,movement['merchant'],movement['product'],move_location)
            if current_stock+movement['quantity']<0:
                loc=require_location(c,movement['merchant'],move_location)
                raise ValueError(f"Insufficient stock at {loc['name']}. Confirm stock received or correct the sale quantity.")
            location_value=location_transfer_value(c,movement['merchant'],movement['product'],move_location)
            sale_cost=None
            if location_value is not None and current_stock>0:
                from decimal import Decimal, ROUND_HALF_UP
                sale_cost=int((Decimal(location_value)*Decimal(-movement['quantity'])/Decimal(current_stock)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            c.execute("UPDATE stock_moves SET status='confirmed',cost_total=?,location_id=? WHERE entry_id=?",(sale_cost,move_location,ident))

        confirmed_by=actor['id'] if actor else None
        c.execute("UPDATE entries SET status='confirmed',confirmed=CURRENT_TIMESTAMP,confirmed_by=? WHERE id=?",(confirmed_by,ident))
        audit(c,actor,'entry.confirm','entry',ident,{'kind':row['kind'],'location_id':location_id})



def _name_key(value):
    import re
    return ' '.join(re.findall(r"[a-z0-9]+", str(value or '').lower()))


def customer_balance(c, merchant, customer):
    return c.execute(
        "SELECT COALESCE(SUM(total-paid+COALESCE(customer_credit,0)),0) FROM entries WHERE merchant=? AND customer=? AND status='confirmed'",
        (merchant, customer)
    ).fetchone()[0]


def customer_credit_balance(c, merchant, customer):
    return c.execute(
        "SELECT COALESCE(SUM(customer_credit),0) FROM entries WHERE merchant=? AND customer=? AND status='confirmed'",
        (merchant, customer)
    ).fetchone()[0]


def resolve_customer(c, merchant, suggested_name):
    """Resolve a parsed customer name without silently guessing ambiguous identities."""
    suggested_name=' '.join(str(suggested_name or '').strip().split())
    empty={'suggested_name':None,'status':'none','auto_select':False,'match':None,'matches':[],'can_create':False}
    if not suggested_name:
        return empty
    rows=[dict(r) for r in c.execute('SELECT id,name FROM customers WHERE merchant=? AND id<>? ORDER BY name,id',(merchant,WALK_IN_CUSTOMER_ID))]
    for r in rows:
        r['balance']=customer_balance(c,merchant,r['id'])
        r['credit']=customer_credit_balance(c,merchant,r['id'])
    key=_name_key(suggested_name)
    exact=[r for r in rows if _name_key(r['name'])==key]
    if len(exact)==1:
        return {'suggested_name':suggested_name,'status':'exact','auto_select':True,'match':exact[0],'matches':exact,'can_create':False}
    if len(exact)>1:
        return {'suggested_name':suggested_name,'status':'ambiguous','auto_select':False,'match':None,'matches':exact,'can_create':False}

    wanted=key.split()
    token_matches=[]
    if wanted:
        for r in rows:
            tokens=_name_key(r['name']).split()
            if all(w in tokens for w in wanted):
                token_matches.append(r)
    if len(token_matches)==1:
        return {'suggested_name':suggested_name,'status':'unique_name_part','auto_select':True,'match':token_matches[0],'matches':token_matches,'can_create':False}
    if len(token_matches)>1:
        return {'suggested_name':suggested_name,'status':'ambiguous','auto_select':False,'match':None,'matches':token_matches[:8],'can_create':False}

    from difflib import SequenceMatcher
    similar=[]
    for r in rows:
        score=SequenceMatcher(None,key,_name_key(r['name'])).ratio() if key else 0
        if score>=0.72:
            item=dict(r);item['score']=round(score,3);similar.append(item)
    similar.sort(key=lambda x:(-x['score'],x['name'].lower(),x['id']))
    if similar:
        return {'suggested_name':suggested_name,'status':'similar','auto_select':False,'match':None,'matches':similar[:5],'can_create':True}
    return {'suggested_name':suggested_name,'status':'new','auto_select':False,'match':None,'matches':[],'can_create':True}



def parse_for_review(merchant, text, selected_kind=None):
    parsed=voice.parse(text, selected_kind)
    resolution={'suggested_name':None,'status':'none','auto_select':False,'match':None,'matches':[],'can_create':False}
    supplier_resolution={'suggested_name':None,'status':'none','auto_select':False,'match':None,'matches':[],'can_create':False}
    merchant=str(merchant or '').strip()
    if merchant and parsed.get('customer_name'):
        with connect() as c:
            resolution=resolve_customer(c,merchant,parsed.get('customer_name'))
        if parsed.get('kind')=='repayment':
            resolution['can_create']=False
            if resolution['status']=='new':
                resolution['status']='repayment_customer_not_found'
            if resolution.get('match') is not None:
                parsed['customer_balance']=resolution['match'].get('balance',0)
        elif resolution.get('match') is not None:
            parsed['customer_balance']=resolution['match'].get('balance',0)
    parsed['customer_resolution']=resolution

    if merchant and parsed.get('supplier_name'):
        with connect() as c:
            supplier_resolution=resolve_supplier(c,merchant,parsed.get('supplier_name'))
    parsed['supplier_resolution']=supplier_resolution

    location_resolution={'suggested_name':None,'status':'none','auto_select':False,'match':None,'matches':[],'can_create':False}
    if merchant and parsed.get('kind')=='stock' and parsed.get('stock_movement') in ('distributed','returned_in') and parsed.get('destination'):
        with connect() as c:
            location_resolution=resolve_location(c,merchant,parsed.get('destination'))
    parsed['location_resolution']=location_resolution
    return parsed


def create_customer_from_suggestion(merchant,name):
    merchant=str(merchant or '').strip();name=' '.join(str(name or '').strip().split())
    if not merchant or not name or len(name)>200:
        raise ValueError('Shop and suggested customer name are required.')
    if name.casefold()==WALK_IN_CUSTOMER_NAME.casefold():
        raise ValueError('Use the Walk-in / Cash Customer option instead of creating it as a named customer.')
    import re, unicodedata
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM shops WHERE id=?',(merchant,)).fetchone():
            raise ValueError('Create or select the shop first.')
        exact=c.execute('SELECT id,name FROM customers WHERE merchant=? AND lower(name)=lower(?)',(merchant,name)).fetchone()
        if exact:
            return dict(exact)
        ascii_name=unicodedata.normalize('NFKD',name).encode('ascii','ignore').decode('ascii').lower()
        base=re.sub(r'[^a-z0-9]+','-',ascii_name).strip('-') or 'customer'
        base=base[:160]
        ident=base;n=2
        while c.execute('SELECT 1 FROM customers WHERE merchant=? AND id=?',(merchant,ident)).fetchone():
            suffix='-'+str(n);ident=base[:200-len(suffix)]+suffix;n+=1
        c.execute('INSERT INTO customers VALUES(?,?,?)',(merchant,ident,name))
        return {'id':ident,'name':name}


def customer(data):



    values=[str(data.get(k,'')).strip() for k in ('merchant','id','name')]



    if not all(values) or any(len(v)>200 for v in values): raise ValueError('Business, customer ID and name are required (maximum 200 characters).')
    if values[1]==WALK_IN_CUSTOMER_ID or values[2].casefold()==WALK_IN_CUSTOMER_NAME.casefold():
        raise ValueError('That customer is reserved for the Walk-in / Cash Customer option.')



    with connect() as c:



        if c.execute('SELECT 1 FROM customers WHERE merchant=? AND id=?',values[:2]).fetchone():



            raise ValueError('Customer ID already exists in this business. Select that customer.')



        c.execute('INSERT INTO customers VALUES(?,?,?)',values)







def reverse(ident,reason,actor=None):
    reason=str(reason or '').strip()
    if not reason or len(reason)>2000:
        raise ValueError('Give a correction reason (maximum 2000 characters).')

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT * FROM entries WHERE id=?',(ident,)).fetchone()
        if not r:
            raise ValueError('Entry not found.')
        if r['status'] in ('reversed','cancelled'):
            return

        location_id=r['location_id'] or MAIN_LOCATION_ID

        if r['status']=='draft':
            if actor:
                if actor['role'] in AUTH_MANAGE_ROLES:
                    pass
                elif r['kind']=='sale' and actor['role']=='branch_manager':
                    if not actor_can_use_location(actor,location_id):
                        raise PermissionError('This draft belongs to a location outside your assignment.')
                elif r['kind']=='sale' and actor['role']=='cashier':
                    if not actor_can_use_location(actor,location_id):
                        raise PermissionError('This draft belongs to a location outside your assignment.')
                    if r['created_by'] and r['created_by']!=actor['id']:
                        raise PermissionError('Cashiers can cancel only sale drafts they created.')
                else:
                    raise PermissionError('Your role cannot cancel this draft.')
        else:
            if actor and actor['role'] not in AUTH_MANAGE_ROLES:
                raise PermissionError('Only an owner or administrator can reverse a confirmed entry in M6 Phase 1.')

        if r['status']=='confirmed':
            balance=customer_balance(c,r['merchant'],r['customer'])
            contribution=r['total']-r['paid']+int(r['customer_credit'] or 0)
            if balance-contribution<0:
                raise ValueError('This would make the customer balance negative. Reverse related repayments first, then correct and re-enter the transactions.')

        status='cancelled' if r['status']=='draft' else 'reversed'
        c.execute('INSERT INTO reversals(entry_id,reason) VALUES(?,?)',(ident,reason))
        c.execute('UPDATE entries SET status=? WHERE id=?',(status,ident))
        c.execute('UPDATE stock_moves SET status=?,reason=? WHERE entry_id=?',(status,reason,ident))
        audit(c,actor,'entry.cancel' if status=='cancelled' else 'entry.reverse','entry',ident,{
            'reason':reason,'location_id':location_id,'previous_status':r['status']
        })



def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

def _iso_utc(dt):
    return dt.isoformat(timespec='seconds')

def _password_digest(password, salt_hex=None):
    password=str(password or '')
    if len(password)<10:
        raise ValueError('Password must be at least 10 characters.')
    if len(password)>200:
        raise ValueError('Password is too long.')
    salt=bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,AUTH_PBKDF2_ROUNDS)
    return salt.hex(),digest.hex()

def auth_status():
    with connect() as c:
        count=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']
        shops=[dict(r) for r in c.execute('SELECT id,name FROM shops ORDER BY name,id')]
    return {'needs_setup':count==0,'shops':shops}

def _public_user(c,row):
    if not row:return None
    u={'merchant':row['merchant'],'id':row['id'],'name':row['name'],'username':row['username'],
       'role':row['role'],'active':bool(row['active']),
       'must_change_password':bool(row['must_change_password']) if 'must_change_password' in row.keys() else False,
       'capabilities':list(ROLE_CAPABILITIES.get(row['role'],[])),'locations':[]}
    u['locations']=[dict(r) for r in c.execute(
        'SELECT l.id,l.name FROM user_locations ul JOIN locations l '
        'ON l.merchant=ul.merchant AND l.id=ul.location_id '
        'WHERE ul.merchant=? AND ul.user_id=? ORDER BY l.name,l.id',
        (row['merchant'],row['id']))]
    return u

def _session_token_hash(token):
    return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()

def audit(c,user,action,target_type=None,target_id=None,details=None):
    if not user:return
    c.execute('INSERT INTO audit_log(id,merchant,user_id,role,action,target_type,target_id,details) VALUES(?,?,?,?,?,?,?,?)',
      (str(uuid.uuid4()),user['merchant'],user['id'],user['role'],action,target_type,target_id,
       json.dumps(details,ensure_ascii=False,sort_keys=True) if details is not None else None))

def auth_event(c,action,merchant=None,username=None,user_id=None,details=None):
    c.execute(
        'INSERT INTO auth_events(id,merchant,username,user_id,action,details) VALUES(?,?,?,?,?,?)',
        (
            str(uuid.uuid4()),merchant,str(username or '').strip() or None,user_id,action,
            json.dumps(details,ensure_ascii=False,sort_keys=True) if details is not None else None
        )
    )

def _active_owner_count(c,merchant):
    return c.execute(
        "SELECT COUNT(*) n FROM users WHERE merchant=? AND role='owner' AND active=1",
        (merchant,)
    ).fetchone()['n']

def _target_user(c,actor,user_id):
    row=c.execute(
        'SELECT * FROM users WHERE merchant=? AND id=?',
        (actor['merchant'],str(user_id or '').strip())
    ).fetchone()
    if not row:
        raise ValueError('User account not found.')
    return row

def _validate_user_locations(c,merchant,role,locations):
    if not isinstance(locations,list):
        raise ValueError('Location assignments must be a list.')
    cleaned=[]
    for raw in locations:
        loc=str(raw or '').strip()
        if loc and loc not in cleaned:
            cleaned.append(loc)
    if role in ('branch_manager','cashier','inventory_officer') and not cleaned:
        raise ValueError('Assign at least one branch/location to this role.')
    for loc in cleaned:
        if not c.execute('SELECT 1 FROM locations WHERE merchant=? AND id=?',(merchant,loc)).fetchone():
            raise ValueError('One selected location is not registered for this business.')
    return cleaned

def _revoke_user_sessions(c,merchant,user_id):
    c.execute('DELETE FROM auth_sessions WHERE merchant=? AND user_id=?',(merchant,user_id))

def security_activity_for(user,limit=50):
    if not user or user['role'] not in AUTH_MANAGE_ROLES:
        return []
    limit=max(1,min(int(limit),200))
    with connect() as c:
        rows=[]
        for r in c.execute(
            'SELECT created,action,user_id,target_type,target_id,details,role '
            'FROM audit_log WHERE merchant=? AND (action LIKE \'auth.%\' OR action LIKE \'user.%\') '
            'ORDER BY created DESC,rowid DESC LIMIT ?',(user['merchant'],limit)
        ):
            d=dict(r);d['source']='audit';rows.append(d)
        for r in c.execute(
            'SELECT created,action,user_id,username,details FROM auth_events '
            'WHERE merchant=? ORDER BY created DESC,rowid DESC LIMIT ?',(user['merchant'],limit)
        ):
            d=dict(r);d['source']='auth_event';rows.append(d)
        rows.sort(key=lambda x:x.get('created') or '',reverse=True)
        return rows[:limit]

def financial_control_activity_for(user,limit=100):
    if not user:
        return []

    if user['role'] not in AUTH_MANAGE_ROLES and user['role']!='branch_manager':
        return []

    limit=max(1,min(int(limit),200))
    merchant=user['merchant']

    with connect() as c:
        rows=[]

        for r in c.execute(
            "SELECT a.created,a.action,a.user_id,a.role,"
            "a.target_type,a.target_id,a.details,"
            "u.name user_name "
            "FROM audit_log a "
            "LEFT JOIN users u "
            "ON u.merchant=a.merchant AND u.id=a.user_id "
            "WHERE a.merchant=? "
            "AND ("
            "a.action LIKE 'payment.%' "
            "OR a.action LIKE 'reconciliation.%' "
            "OR a.action LIKE 'daily_closing.%'"
            ") "
            "ORDER BY a.created DESC,a.rowid DESC "
            "LIMIT ?",
            (merchant,limit)
        ):
            item=dict(r)

            raw=item.get('details')
            try:
                details=json.loads(raw) if raw else {}
            except Exception:
                details={}

            item['details']=details
            item['location_id']=str(
                details.get('location_id') or ''
            ).strip() or None

            # Resolve location for older audit events where it was not
            # written directly into the JSON details.
            if not item['location_id']:
                target_type=item.get('target_type')
                target_id=item.get('target_id')

                if target_type=='payment_record' and target_id:
                    x=c.execute(
                        "SELECT location_id FROM payment_records "
                        "WHERE merchant=? AND id=?",
                        (merchant,target_id)
                    ).fetchone()
                    if x:
                        item['location_id']=x['location_id']

                elif target_type=='reconciliation_session' and target_id:
                    x=c.execute(
                        "SELECT location_id FROM reconciliation_sessions "
                        "WHERE merchant=? AND id=?",
                        (merchant,target_id)
                    ).fetchone()
                    if x:
                        item['location_id']=x['location_id']

                elif target_type=='daily_closing' and target_id:
                    x=c.execute(
                        "SELECT location_id FROM daily_closings "
                        "WHERE merchant=? AND id=?",
                        (merchant,target_id)
                    ).fetchone()
                    if x:
                        item['location_id']=x['location_id']

                elif target_type=='reconciliation_match':
                    session_id=str(
                        details.get('session_id') or ''
                    ).strip()

                    if session_id:
                        x=c.execute(
                            "SELECT location_id FROM reconciliation_sessions "
                            "WHERE merchant=? AND id=?",
                            (merchant,session_id)
                        ).fetchone()
                        if x:
                            item['location_id']=x['location_id']

            rows.append(item)

        if user['role']=='branch_manager':
            allowed=actor_location_ids(user)

            rows=[
                r for r in rows
                if r.get('location_id') in allowed
            ]

        return rows[:limit]


def create_session(c,merchant,user_id):
    from datetime import timedelta
    token=secrets.token_urlsafe(40)
    expires=_utc_now()+timedelta(hours=AUTH_SESSION_HOURS)
    c.execute('INSERT INTO auth_sessions(token_hash,merchant,user_id,expires) VALUES(?,?,?,?)',
              (_session_token_hash(token),merchant,user_id,_iso_utc(expires)))
    return token

def authenticate_token(token):
    token=str(token or '').strip()
    if not token:return None
    with connect() as c:
        row=c.execute('SELECT u.* FROM auth_sessions s JOIN users u '
                      'ON u.merchant=s.merchant AND u.id=s.user_id '
                      'WHERE s.token_hash=? AND s.expires>? AND u.active=1',
                      (_session_token_hash(token),_iso_utc(_utc_now()))).fetchone()
        return _public_user(c,row) if row and row['role'] in AUTH_ROLES else None

def bootstrap_owner(data):
    merchant=str(data.get('merchant','')).strip()
    name=' '.join(str(data.get('name','')).strip().split())
    username=str(data.get('username','')).strip()
    password=str(data.get('password',''))
    if not merchant or not name or not username:
        raise ValueError('Business, owner name, username and password are required.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise ValueError('Owner setup is already complete. Sign in instead.')
        if not c.execute('SELECT 1 FROM shops WHERE id=?',(merchant,)).fetchone():
            raise ValueError('Choose an existing business.')
        salt,digest=_password_digest(password)
        c.execute('INSERT INTO users(merchant,id,name,username,password_salt,password_hash,role) VALUES(?,?,?,?,?,?,?)',
                  (merchant,'owner',name,username,salt,digest,'owner'))
        token=create_session(c,merchant,'owner')
        row=c.execute('SELECT * FROM users WHERE merchant=? AND id=?',(merchant,'owner')).fetchone()
        user=_public_user(c,row);audit(c,user,'auth.bootstrap','user','owner',{'username':username})
        return {'token':token,'user':user}

def login_user(data):
    username=str(data.get('username','')).strip()
    password=str(data.get('password',''))
    if not username or not password:
        raise ValueError('Enter username and password.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE',(username,)).fetchone()
        if not row:
            auth_event(c,'auth.login_failed',username=username,details={'reason':'unknown_username'})
            raise ValueError('Invalid username or password.')
        if not row['active']:
            auth_event(c,'auth.login_blocked',row['merchant'],username,row['id'],{'reason':'inactive'})
            raise ValueError('This account is disabled. Contact the business owner or administrator.')
        _,digest=_password_digest(password,row['password_salt'])
        if not hmac.compare_digest(digest,row['password_hash']):
            auth_event(c,'auth.login_failed',row['merchant'],username,row['id'],{'reason':'bad_password'})
            user=_public_user(c,row)
            audit(c,user,'auth.login_failed','user',row['id'],None)
            raise ValueError('Invalid username or password.')
        c.execute('DELETE FROM auth_sessions WHERE expires<=?',(_iso_utc(_utc_now()),))
        token=create_session(c,row['merchant'],row['id'])
        user=_public_user(c,row)
        audit(c,user,'auth.login','user',row['id'],None)
        auth_event(c,'auth.login',row['merchant'],username,row['id'],None)
        return {'token':token,'user':user}


def logout_user(token,user):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM auth_sessions WHERE token_hash=?',(_session_token_hash(token),))
        audit(c,user,'auth.logout','user',user['id'],None)
        auth_event(c,'auth.logout',user['merchant'],user['username'],user['id'],None)

def list_users_for(user):
    if not user or user['role'] not in AUTH_MANAGE_ROLES:return []
    with connect() as c:
        return [_public_user(c,r) for r in c.execute(
            'SELECT * FROM users WHERE merchant=? ORDER BY name,username',(user['merchant'],))]

def create_user(actor,data):
    if not actor or actor['role'] not in AUTH_MANAGE_ROLES:
        raise PermissionError('Only an owner or administrator can create users.')
    name=' '.join(str(data.get('name','')).strip().split())
    username=str(data.get('username','')).strip()
    password=str(data.get('password',''))
    role=str(data.get('role','')).strip()
    locations=data.get('location_ids') or []
    if not isinstance(locations,list):raise ValueError('Location assignments must be a list.')
    locations=[str(x).strip() for x in locations if str(x).strip()]
    if not name or not username or role not in AUTH_ROLES:raise ValueError('Name, username and a valid role are required.')
    if role=='owner' and actor['role']!='owner':raise PermissionError('Only the owner can create another owner account.')
    if role in ('branch_manager','cashier','inventory_officer') and not locations:
        raise ValueError('Assign at least one branch/location to this role.')
    salt,digest=_password_digest(password)
    base=re.sub(r'[^a-z0-9]+','-',username.lower()).strip('-') or 'user';ident=base[:120]
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for loc in locations:
            if not c.execute('SELECT 1 FROM locations WHERE merchant=? AND id=?',(actor['merchant'],loc)).fetchone():
                raise ValueError('One selected location is not registered for this business.')
        n=2;original=ident
        while c.execute('SELECT 1 FROM users WHERE merchant=? AND id=?',(actor['merchant'],ident)).fetchone():
            suffix='-'+str(n);ident=original[:120-len(suffix)]+suffix;n+=1
        try:
            c.execute(
                'INSERT INTO users(merchant,id,name,username,password_salt,password_hash,role,must_change_password) '
                'VALUES(?,?,?,?,?,?,?,1)',
                (actor['merchant'],ident,name,username,salt,digest,role)
            )
        except sqlite3.IntegrityError:raise ValueError('That username is already in use.')
        for loc in locations:
            c.execute('INSERT INTO user_locations(merchant,user_id,location_id) VALUES(?,?,?)',
                      (actor['merchant'],ident,loc))
        audit(c,actor,'user.create','user',ident,{'role':role,'locations':locations})
        return _public_user(c,c.execute('SELECT * FROM users WHERE merchant=? AND id=?',
                                       (actor['merchant'],ident)).fetchone())

def update_user(actor,data):
    if not actor or actor['role'] not in AUTH_MANAGE_ROLES:
        raise PermissionError('Only an owner or administrator can edit users.')
    user_id=str(data.get('id','')).strip()
    name=' '.join(str(data.get('name','')).strip().split())
    role=str(data.get('role','')).strip()
    locations=data.get('location_ids') or []
    if not user_id or not name or role not in AUTH_ROLES:
        raise ValueError('User, name and a valid role are required.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=_target_user(c,actor,user_id)
        old_role=row['role']
        old_locations=[r['location_id'] for r in c.execute(
            'SELECT location_id FROM user_locations WHERE merchant=? AND user_id=? ORDER BY location_id',
            (actor['merchant'],user_id)
        )]
        locations=_validate_user_locations(c,actor['merchant'],role,locations)
        if row['role']=='owner' and actor['role']!='owner':
            raise PermissionError('Administrators cannot modify an owner account.')
        if role=='owner' and actor['role']!='owner':
            raise PermissionError('Only an owner can assign the owner role.')
        if user_id==actor['id'] and role!=row['role']:
            raise ValueError('You cannot change your own role while signed in.')
        if row['role']=='owner' and role!='owner' and row['active'] and _active_owner_count(c,actor['merchant'])<=1:
            raise ValueError('The only active owner cannot be demoted.')
        c.execute(
            'UPDATE users SET name=?,role=?,updated=CURRENT_TIMESTAMP WHERE merchant=? AND id=?',
            (name,role,actor['merchant'],user_id)
        )
        c.execute('DELETE FROM user_locations WHERE merchant=? AND user_id=?',(actor['merchant'],user_id))
        for loc in locations:
            c.execute('INSERT INTO user_locations(merchant,user_id,location_id) VALUES(?,?,?)',
                      (actor['merchant'],user_id,loc))
        changed_scope=(old_role!=role or old_locations!=sorted(locations))
        if changed_scope:
            _revoke_user_sessions(c,actor['merchant'],user_id)
        audit(c,actor,'user.update','user',user_id,{
            'old_role':old_role,'role':role,'old_locations':old_locations,
            'locations':locations,'sessions_revoked':changed_scope
        })
        return _public_user(c,c.execute(
            'SELECT * FROM users WHERE merchant=? AND id=?',(actor['merchant'],user_id)
        ).fetchone())

def set_user_active(actor,data):
    if not actor or actor['role'] not in AUTH_MANAGE_ROLES:
        raise PermissionError('Only an owner or administrator can change account status.')
    user_id=str(data.get('id','')).strip()
    active=bool(data.get('active'))
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=_target_user(c,actor,user_id)
        if row['role']=='owner' and actor['role']!='owner':
            raise PermissionError('Administrators cannot change an owner account.')
        if not active and user_id==actor['id']:
            raise ValueError('You cannot disable the account you are currently using.')
        if not active and row['role']=='owner' and row['active'] and _active_owner_count(c,actor['merchant'])<=1:
            raise ValueError('The only active owner cannot be disabled.')
        c.execute('UPDATE users SET active=?,updated=CURRENT_TIMESTAMP WHERE merchant=? AND id=?',
                  (1 if active else 0,actor['merchant'],user_id))
        if not active:
            _revoke_user_sessions(c,actor['merchant'],user_id)
        audit(c,actor,'user.reactivate' if active else 'user.deactivate','user',user_id,None)
        return _public_user(c,c.execute(
            'SELECT * FROM users WHERE merchant=? AND id=?',(actor['merchant'],user_id)
        ).fetchone())

def reset_user_password(actor,data):
    if not actor or actor['role'] not in AUTH_MANAGE_ROLES:
        raise PermissionError('Only an owner or administrator can reset staff passwords.')
    user_id=str(data.get('id','')).strip()
    password=str(data.get('password',''))
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=_target_user(c,actor,user_id)
        if user_id==actor['id']:
            raise ValueError('Use Change password for your own account.')
        if row['role']=='owner' and actor['role']!='owner':
            raise PermissionError('Administrators cannot reset an owner password.')
        salt,digest=_password_digest(password)
        c.execute(
            'UPDATE users SET password_salt=?,password_hash=?,must_change_password=1,updated=CURRENT_TIMESTAMP '
            'WHERE merchant=? AND id=?',
            (salt,digest,actor['merchant'],user_id)
        )
        _revoke_user_sessions(c,actor['merchant'],user_id)
        audit(c,actor,'user.password_reset','user',user_id,{'force_change':True})
        auth_event(c,'auth.password_reset',actor['merchant'],row['username'],user_id,{'by_user_id':actor['id']})
        return {'ok':True}

def change_own_password(actor,current_token,data):
    if not actor:
        raise PermissionError('Authentication required.')
    current=str(data.get('current_password',''))
    new_password=str(data.get('new_password',''))
    confirm=str(data.get('confirm_password',''))
    if new_password!=confirm:
        raise ValueError('New password and confirmation do not match.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM users WHERE merchant=? AND id=? AND active=1',
                      (actor['merchant'],actor['id'])).fetchone()
        if not row:
            raise PermissionError('Account is not active.')
        _,current_digest=_password_digest(current,row['password_salt'])
        if not hmac.compare_digest(current_digest,row['password_hash']):
            auth_event(c,'auth.password_change_failed',actor['merchant'],row['username'],row['id'],{'reason':'bad_current_password'})
            raise ValueError('Current password is incorrect.')
        _,new_on_old_salt=_password_digest(new_password,row['password_salt'])
        if hmac.compare_digest(new_on_old_salt,row['password_hash']):
            raise ValueError('Choose a password different from the current password.')
        salt,digest=_password_digest(new_password)
        c.execute(
            'UPDATE users SET password_salt=?,password_hash=?,must_change_password=0,updated=CURRENT_TIMESTAMP '
            'WHERE merchant=? AND id=?',
            (salt,digest,actor['merchant'],actor['id'])
        )
        _revoke_user_sessions(c,actor['merchant'],actor['id'])
        token=create_session(c,actor['merchant'],actor['id'])
        fresh=c.execute('SELECT * FROM users WHERE merchant=? AND id=?',
                        (actor['merchant'],actor['id'])).fetchone()
        user=_public_user(c,fresh)
        audit(c,user,'auth.password_change','user',actor['id'],None)
        auth_event(c,'auth.password_change',actor['merchant'],row['username'],actor['id'],None)
        return {'token':token,'user':user}

def filter_state_for_user(payload,user):
    merchant=user['merchant'];out=dict(payload)
    for key in ('inbox','products','suppliers','locations','location_inventory','transfers',
                'movements','entries','balances','customers','summary','payment_records','reconciliation_sessions','daily_closings'):
        if key in out:out[key]=[x for x in out[key] if x.get('merchant')==merchant]
    if 'shops' in out:out['shops']=[x for x in out['shops'] if x.get('id')==merchant]
    if 'voice_jobs' in out:out['voice_jobs']=[x for x in out['voice_jobs'] if x.get('merchant')==merchant]
    if user['role'] in ('branch_manager','cashier','inventory_officer'):
        allowed=actor_location_ids(user)
        out['entries']=[x for x in out.get('entries',[]) if x.get('location_id') in allowed]
        out['location_inventory']=[x for x in out.get('location_inventory',[]) if x.get('location_id') in allowed]
        out['transfers']=[x for x in out.get('transfers',[]) if x.get('from_location') in allowed or x.get('to_location') in allowed]
        out['movements']=[x for x in out.get('movements',[]) if (x.get('location_id') or MAIN_LOCATION_ID) in allowed]
        allowed_locations=actor_location_ids(user)
        out['branch_prices']=[p for p in out.get('branch_prices',[]) if p.get('location_id') in allowed_locations]
        out['branch_price_history']=[p for p in out.get('branch_price_history',[]) if p.get('location_id') in allowed_locations]

        out['payment_records']=[
            p for p in out.get('payment_records',[])
            if p.get('location_id') in allowed_locations
        ]

        out['reconciliation_sessions']=[
            r for r in out.get('reconciliation_sessions',[])
            if r.get('location_id') in allowed_locations
        ]

        out['daily_closings']=[
            r for r in out.get('daily_closings',[])
            if r.get('location_id') in allowed_locations
        ]

        if user['role']=='cashier':
            out['payment_records']=[
                p for p in out.get('payment_records',[])
                if p.get('submitted_by')==user['id']
            ]

        out['summary']=branch_summary(out.get('entries',[]),merchant)
        out['balances']=[]
        for p in out.get('products',[]):
            p['balance']=None
            p['inventory_value']=None
            p['avg_unit_cost']=None
            p['costing_complete']=None
    if user['role'] not in AUTH_MANAGE_ROLES and user['role'] not in ('branch_manager','cashier'):
        out['payment_records']=[]

    if user['role'] not in AUTH_MANAGE_ROLES and user['role']!='branch_manager':
        out['reconciliation_sessions']=[]
        out['daily_closings']=[]

    out['users']=list_users_for(user)
    out['current_user']=user
    out['role_permissions']={k:list(v) for k,v in ROLE_CAPABILITIES.items()} if user['role'] in AUTH_MANAGE_ROLES else {}
    out['security_events']=security_activity_for(user,50) if user['role'] in AUTH_MANAGE_ROLES else []
    out['financial_control_events']=financial_control_activity_for(user,100)
    out['m5_phase']='m5b-access-hardening+m6b-branch-inventory'
    return out

def _record_actor_merchant(actor):
    """Identity supplied by authenticated HTTP code, never request JSON."""
    if (not isinstance(actor, dict) or not actor.get('id')
            or not isinstance(actor.get('merchant'), str) or not actor['merchant'].strip()
            or actor.get('role') not in AUTH_ROLES
            or actor.get('must_change_password') or actor.get('active') is False):
        raise PermissionError('Authenticated actor required.')
    return actor['merchant']


def require_actor_merchant(user,data):
    verified_merchant = _record_actor_merchant(user)
    merchant=str(data.get('merchant','')).strip()
    if merchant and merchant!=verified_merchant:raise PermissionError('This account cannot access another business.')
    data['merchant'] = verified_merchant
def actor_location_ids(user):
    return {str(x.get('id')) for x in (user.get('locations') or []) if x.get('id')}

def actor_can_use_location(user, location_id):
    location_id=str(location_id or '').strip()
    if not user:
        return False
    if user['role'] in AUTH_MANAGE_ROLES:
        return True
    return location_id in actor_location_ids(user)

def require_sale_actor(user, location_id):
    if not user:
        raise PermissionError('Authentication required.')
    if user['role'] not in ('owner','admin','branch_manager','cashier'):
        raise PermissionError('Your role cannot create or confirm sales.')
    if not actor_can_use_location(user,location_id):
        raise PermissionError('This account is not assigned to the selected sales location.')

def require_mutation_role(user):
    if user['role'] not in MUTATION_ROLES:
        raise PermissionError('Your role is not permitted to make this ledger change.')


def require_stock_actor(user, location_id, kind, action='draft'):
    if not user:
        raise PermissionError('Authentication required.')
    role=user['role']
    if role in AUTH_MANAGE_ROLES:
        return
    if role not in ('branch_manager','inventory_officer'):
        raise PermissionError('Your role cannot create or confirm inventory movements.')
    if not actor_can_use_location(user,location_id):
        raise PermissionError('This account is not assigned to the selected stock location.')
    if kind=='opening':
        raise PermissionError('Opening stock is restricted to an owner or administrator.')
    if action=='reverse_confirmed':
        raise PermissionError('Only an owner or administrator can reverse a confirmed inventory movement.')

def require_transfer_actor(user, from_location, to_location, action='draft', confirmed=False):
    if not user:
        raise PermissionError('Authentication required.')
    if user['role'] in AUTH_MANAGE_ROLES:
        return
    if user['role'] not in ('branch_manager','inventory_officer'):
        raise PermissionError('Your role cannot manage internal stock transfers.')
    allowed=actor_location_ids(user)
    if from_location not in allowed or to_location not in allowed:
        raise PermissionError('Branch staff can transfer stock only between locations assigned to their account.')
    if confirmed and action=='reverse':
        raise PermissionError('Only an owner or administrator can reverse a confirmed internal transfer.')

def branch_summary(entries, merchant):
    confirmed=[e for e in entries if e.get('status')=='confirmed']
    if not confirmed:
        return []
    return [{
        'merchant':merchant,
        'sales':sum(int(e.get('total') or 0) for e in confirmed),
        'collections':sum(int(e.get('paid') or 0) for e in confirmed),
        'debt':sum(int(e.get('total') or 0)-int(e.get('paid') or 0)+int(e.get('customer_credit') or 0) for e in confirmed),
        'customer_credit':sum(int(e.get('customer_credit') or 0) for e in confirmed),
        'scope':'Assigned locations'
    }]


def state():



    with connect() as c:



        entries=[dict(r) for r in c.execute(
            'SELECT e.*,r.reason,m.product,-m.quantity quantity,m.cost_total cogs,m.location_id movement_location '
            'FROM entries e LEFT JOIN reversals r ON e.id=r.entry_id LEFT JOIN stock_moves m ON m.entry_id=e.id '
            'ORDER BY e.created DESC,e.rowid DESC'
        )]
        for e in entries:
            effective=e.get('location_id') or e.get('movement_location') or MAIN_LOCATION_ID
            e['location_id']=effective
            if effective==MAIN_LOCATION_ID:
                e['location_name']=e['merchant']+'  - Main stock'
            else:
                loc=c.execute('SELECT name FROM locations WHERE merchant=? AND id=?',(e['merchant'],effective)).fetchone()
                e['location_name']=loc['name'] if loc else effective
            creator=c.execute('SELECT name FROM users WHERE merchant=? AND id=?',(e['merchant'],e.get('created_by'))).fetchone() if e.get('created_by') else None
            confirmer=c.execute('SELECT name FROM users WHERE merchant=? AND id=?',(e['merchant'],e.get('confirmed_by'))).fetchone() if e.get('confirmed_by') else None
            e['created_by_name']=creator['name'] if creator else None
            e['confirmed_by_name']=confirmer['name'] if confirmer else None
            e['gross_profit']=(e['total']-e['cogs']) if e['kind']=='sale' and e['product'] and e['cogs'] is not None else None
            e['unit_sale_price']=int(round(e['total']*1000/e['quantity'])) if e['kind']=='sale' and e['product'] and e['quantity'] else None



        balances=[dict(r) for r in c.execute("SELECT merchant,customer,SUM(total-paid+COALESCE(customer_credit,0)) balance,SUM(COALESCE(customer_credit,0)) credit FROM entries WHERE status='confirmed' AND customer<>? GROUP BY merchant,customer",(WALK_IN_CUSTOMER_ID,))]



        customers=[dict(r) for r in c.execute('SELECT * FROM customers ORDER BY name')]
        for customer_row in customers:
            customer_row['balance']=0 if customer_row['id']==WALK_IN_CUSTOMER_ID else customer_balance(c,customer_row['merchant'],customer_row['id'])
            customer_row['credit']=0 if customer_row['id']==WALK_IN_CUSTOMER_ID else customer_credit_balance(c,customer_row['merchant'],customer_row['id'])



        summary=[dict(r) for r in c.execute("SELECT merchant,SUM(total) sales,SUM(paid) collections,SUM(total-paid+COALESCE(customer_credit,0)) debt,SUM(COALESCE(customer_credit,0)) customer_credit FROM entries WHERE status='confirmed' GROUP BY merchant")]



        payment_records=[]
        for r in c.execute(
            "SELECT pr.*, "
            "l.name location_name, "
            "u.name submitted_by_name, "
            "e.kind entry_kind, "
            "e.description entry_description, "
            "e.customer entry_customer, "
            "e.paid entry_paid "
            "FROM payment_records pr "
            "LEFT JOIN locations l "
            "ON l.merchant=pr.merchant AND l.id=pr.location_id "
            "LEFT JOIN users u "
            "ON u.merchant=pr.merchant AND u.id=pr.submitted_by "
            "LEFT JOIN entries e "
            "ON e.merchant=pr.merchant AND e.id=pr.entry_id "
            "ORDER BY COALESCE(pr.payment_date,pr.created) DESC,pr.created DESC"
        ):
            payment_records.append(dict(r))

        reconciliation_sessions=[]
        for r in c.execute(
            "SELECT rs.*, "
            "l.name location_name, "
            "creator.name created_by_name, "
            "approver.name approved_by_name "
            "FROM reconciliation_sessions rs "
            "LEFT JOIN locations l "
            "ON l.merchant=rs.merchant AND l.id=rs.location_id "
            "LEFT JOIN users creator "
            "ON creator.merchant=rs.merchant AND creator.id=rs.created_by "
            "LEFT JOIN users approver "
            "ON approver.merchant=rs.merchant AND approver.id=rs.approved_by "
            "ORDER BY rs.period_end DESC,rs.period_start DESC,rs.created DESC"
        ):
            item=dict(r)

            expected=int(item.get('expected_collections') or 0)
            presented=int(item.get('presented_collections') or 0)
            matched=int(item.get('matched_amount') or 0)
            variance=int(item.get('variance') or 0)

            item['unmatched_evidence']=presented-matched
            item['unmatched_ledger']=expected-matched

            if variance==0:
                item['amount_status']='balanced'
            elif variance<0:
                item['amount_status']='shortfall'
            else:
                item['amount_status']='overage'

            reconciliation_sessions.append(item)

        daily_closings=[]
        for r in c.execute(
            "SELECT dc.*, "
            "l.name location_name, "
            "submitter.name submitted_by_name, "
            "approver.name approved_by_name "
            "FROM daily_closings dc "
            "LEFT JOIN locations l "
            "ON l.merchant=dc.merchant AND l.id=dc.location_id "
            "LEFT JOIN users submitter "
            "ON submitter.merchant=dc.merchant AND submitter.id=dc.submitted_by "
            "LEFT JOIN users approver "
            "ON approver.merchant=dc.merchant AND approver.id=dc.approved_by "
            "ORDER BY dc.business_date DESC,dc.submitted_at DESC"
        ):
            item=dict(r)

            expected=int(item.get('expected_collections') or 0)
            presented=int(item.get('presented_collections') or 0)
            matched=int(item.get('matched_amount') or 0)
            variance=int(item.get('variance') or 0)

            item['unmatched_evidence']=presented-matched
            item['unmatched_ledger']=expected-matched

            if variance==0:
                item['amount_status']='balanced'
            elif variance<0:
                item['amount_status']='shortfall'
            else:
                item['amount_status']='overage'

            daily_closings.append(item)

        branch_prices=[]
        for r in c.execute(
            'SELECT bp.*,p.name product_name,p.base_selling_price AS base_price,l.name location_name,u.name updated_by_name '
            'FROM branch_prices bp '
            'LEFT JOIN products p ON p.merchant=bp.merchant AND p.id=bp.product '
            'LEFT JOIN locations l ON l.merchant=bp.merchant AND l.id=bp.location_id '
            'LEFT JOIN users u ON u.merchant=bp.merchant AND u.id=bp.updated_by '
            'ORDER BY l.name,p.name'
        ):
            branch_prices.append(dict(r))

        branch_price_history=[]
        for r in c.execute(
            'SELECT h.*,p.name product_name,l.name location_name,u.name changed_by_name '
            'FROM branch_price_history h '
            'LEFT JOIN products p ON p.merchant=h.merchant AND p.id=h.product '
            'LEFT JOIN locations l ON l.merchant=h.merchant AND l.id=h.location_id '
            'LEFT JOIN users u ON u.merchant=h.merchant AND u.id=h.changed_by '
            'ORDER BY h.created DESC,h.rowid DESC LIMIT 100'
        ):
            branch_price_history.append(dict(r))

        products=[dict(r) for r in c.execute('SELECT * FROM products ORDER BY name')]



        for p in products:

            valuation=inventory_valuation(c,p['merchant'],p['id'])

            p['balance']=valuation['quantity']

            p['inventory_value']=valuation['inventory_value']

            p['avg_unit_cost']=valuation['avg_unit_cost']

            p['costing_complete']=valuation['costing_complete']



        movements=[dict(r) for r in c.execute(
            'SELECT m.*,s.name supplier_name FROM stock_moves m '
            'LEFT JOIN suppliers s ON s.merchant=m.merchant AND s.id=m.supplier '
            'ORDER BY m.created DESC,m.rowid DESC'
        )]
        suppliers=[dict(r) for r in c.execute('SELECT * FROM suppliers ORDER BY name,id')]
        locations=[dict(r) for r in c.execute('SELECT * FROM locations ORDER BY name,id')]

        transfers=[]
        for r in c.execute(
            'SELECT * FROM stock_transfers ORDER BY created DESC,rowid DESC'
        ):
            item=dict(r)
            item['from_name']=(
                item['merchant']+'  - Main stock'
                if item['from_location']==MAIN_LOCATION_ID else
                (c.execute(
                    'SELECT name FROM locations WHERE merchant=? AND id=?',
                    (item['merchant'],item['from_location'])
                ).fetchone() or {'name':item['from_location']})['name']
            )
            item['to_name']=(
                item['merchant']+'  - Main stock'
                if item['to_location']==MAIN_LOCATION_ID else
                (c.execute(
                    'SELECT name FROM locations WHERE merchant=? AND id=?',
                    (item['merchant'],item['to_location'])
                ).fetchone() or {'name':item['to_location']})['name']
            )
            transfers.append(item)
        location_inventory=location_inventory_rows(c)

        shops=[dict(r) for r in c.execute('SELECT * FROM shops ORDER BY name,id')]



        inbox=[dict(r) for r in c.execute('SELECT i.*,c.target_kind,c.target_id FROM inbox i LEFT JOIN inbox_claims c ON c.inbox_id=i.id ORDER BY i.created DESC,i.rowid DESC')]

        voice_jobs=[dict(r) for r in c.execute('SELECT * FROM voice_jobs ORDER BY created DESC,rowid DESC')]



    return {
        'inbox':inbox,'voice_jobs':voice_jobs,'shops':shops,'products':products,
        'suppliers':suppliers,'locations':locations,'location_inventory':location_inventory,'branch_prices':branch_prices,'branch_price_history':branch_price_history,
        'payment_records':payment_records,
        'reconciliation_sessions':reconciliation_sessions,
        'daily_closings':daily_closings,
        'transfers':transfers,'movements':movements,'entries':entries,'balances':balances,
        'customers':customers,'summary':summary
    }







def quantity(value):



    from decimal import Decimal, InvalidOperation



    try:



        q=Decimal(str(value))



        if not q.is_finite() or q<=0 or q>1000000 or q*1000!=(q*1000).to_integral_value():raise ValueError('Quantity must be positive, at most 1,000,000, with up to 3 decimals.')



        return int(q*1000)



    except InvalidOperation:raise ValueError('Enter a valid quantity.')







def require_product(c,merchant,product):



    if not c.execute('SELECT 1 FROM products WHERE merchant=? AND id=?',(merchant,product)).fetchone():raise ValueError('Select a product belonging to this business.')







def stock_balance(c,merchant,product):



    return c.execute("SELECT COALESCE(SUM(quantity),0) FROM stock_moves WHERE merchant=? AND product=? AND status='confirmed'",(merchant,product)).fetchone()[0]









def inventory_valuation(c,merchant,product):

    """Return current weighted-average valuation for one product."""

    from decimal import Decimal, ROUND_HALF_UP



    rows=c.execute(

        "SELECT quantity,cost_total FROM stock_moves "

        "WHERE merchant=? AND product=? AND status='confirmed' "

        "ORDER BY created,rowid",

        (merchant,product)

    ).fetchall()



    qty=0

    value=0

    complete=True



    for r in rows:

        q=r['quantity']

        cost=r['cost_total']

        qty+=q

        if cost is None:

            complete=False

        elif q>0:

            value+=cost

        elif q<0:

            value-=cost



    if qty<0:

        complete=False



    avg_unit_cost=None

    inventory_value=None



    if complete and qty>0:

        inventory_value=value

        avg_unit_cost=int(

            (Decimal(value)*Decimal(1000)/Decimal(qty)).quantize(

                Decimal('1'),rounding=ROUND_HALF_UP

            )

        )

    elif complete and qty==0:

        inventory_value=0



    return {

        'quantity':qty,

        'inventory_value':inventory_value,

        'avg_unit_cost':avg_unit_cost,

        'costing_complete':complete,

    }





def stock_set_cost(ident,total_cost):

    """Set a missing cost basis once for an incoming stock movement."""

    cost=money(total_cost)

    if cost<=0:

        raise ValueError('Total stock cost must be greater than zero.')



    with connect() as c:

        c.execute('BEGIN IMMEDIATE')

        r=c.execute('SELECT * FROM stock_moves WHERE id=?',(ident,)).fetchone()

        if not r:

            raise ValueError('Stock movement not found.')

        if r['entry_id'] or r['kind'] not in ('opening','received') or r['quantity']<=0:

            raise ValueError('Cost can only be set on opening stock or stock received.')

        if r['status'] not in ('draft','confirmed'):

            raise ValueError('Cost cannot be added to a reversed or cancelled stock movement.')

        if r['cost_total'] is not None:

            raise ValueError('This stock movement already has a cost. Reverse and re-enter it to correct the cost.')

        c.execute('UPDATE stock_moves SET cost_total=? WHERE id=?',(cost,ident))





def product(data):



    fields=[str(data.get(k,'')).strip() for k in ('merchant','id','name','unit')]



    if not all(fields) or any(len(v)>200 for v in fields):raise ValueError('Business, product ID, name and unit are required (maximum 200 characters).')



    with connect() as c:



        if c.execute('SELECT 1 FROM products WHERE merchant=? AND id=?',fields[:2]).fetchone():raise ValueError('Product ID already exists in this business.')



        c.execute('INSERT INTO products VALUES(?,?,?,?)',fields)








def supplier(data):
    fields=[str(data.get(k,'')).strip() for k in ('merchant','id','name')]
    if not all(fields) or any(len(v)>200 for v in fields):
        raise ValueError('Business, supplier code and supplier name are required (maximum 200 characters).')
    with connect() as c:
        if not c.execute('SELECT 1 FROM shops WHERE id=?',(fields[0],)).fetchone():
            raise ValueError('Create or select the shop first.')
        if c.execute('SELECT 1 FROM suppliers WHERE merchant=? AND id=?',fields[:2]).fetchone():
            raise ValueError('Supplier code already exists in this business.')
        if c.execute('SELECT 1 FROM suppliers WHERE merchant=? AND lower(name)=lower(?)',(fields[0],fields[2])).fetchone():
            raise ValueError('A supplier with this name already exists.')
        c.execute('INSERT INTO suppliers(merchant,id,name) VALUES(?,?,?)',fields)


def resolve_supplier(c, merchant, suggested_name):
    suggested_name=' '.join(str(suggested_name or '').strip().split())
    empty={'suggested_name':None,'status':'none','auto_select':False,'match':None,'matches':[],'can_create':False}
    if not suggested_name:
        return empty
    rows=[dict(r) for r in c.execute('SELECT id,name FROM suppliers WHERE merchant=? ORDER BY name,id',(merchant,))]
    key=_name_key(suggested_name)
    exact=[r for r in rows if _name_key(r['name'])==key]
    if len(exact)==1:
        return {'suggested_name':suggested_name,'status':'exact','auto_select':True,'match':exact[0],'matches':exact,'can_create':False}
    if len(exact)>1:
        return {'suggested_name':suggested_name,'status':'ambiguous','auto_select':False,'match':None,'matches':exact,'can_create':False}
    wanted=key.split()
    token_matches=[]
    if wanted:
        for r in rows:
            tokens=_name_key(r['name']).split()
            if all(w in tokens for w in wanted):
                token_matches.append(r)
    if len(token_matches)==1:
        return {'suggested_name':suggested_name,'status':'unique_name_part','auto_select':True,'match':token_matches[0],'matches':token_matches,'can_create':False}
    if len(token_matches)>1:
        return {'suggested_name':suggested_name,'status':'ambiguous','auto_select':False,'match':None,'matches':token_matches[:8],'can_create':False}
    from difflib import SequenceMatcher
    similar=[]
    for r in rows:
        score=SequenceMatcher(None,key,_name_key(r['name'])).ratio() if key else 0
        if score>=0.72:
            item=dict(r); item['score']=round(score,3); similar.append(item)
    similar.sort(key=lambda x:(-x['score'],x['name'].lower(),x['id']))
    if similar:
        return {'suggested_name':suggested_name,'status':'similar','auto_select':False,'match':None,'matches':similar[:5],'can_create':True}
    return {'suggested_name':suggested_name,'status':'new','auto_select':False,'match':None,'matches':[],'can_create':True}


def location(data):
    fields=[str(data.get(k,'')).strip() for k in ('merchant','id','name')]
    if not all(fields) or any(len(v)>200 for v in fields):
        raise ValueError('Business, location code and location name are required (maximum 200 characters).')
    with connect() as c:
        if not c.execute('SELECT 1 FROM shops WHERE id=?',(fields[0],)).fetchone():
            raise ValueError('Create or select the shop first.')
        if c.execute('SELECT 1 FROM locations WHERE merchant=? AND id=?',fields[:2]).fetchone():
            raise ValueError('Location code already exists in this business.')
        if c.execute('SELECT 1 FROM locations WHERE merchant=? AND lower(name)=lower(?)',(fields[0],fields[2])).fetchone():
            raise ValueError('A location with this name already exists.')
        c.execute('INSERT INTO locations(merchant,id,name) VALUES(?,?,?)',fields)


def resolve_location(c, merchant, suggested_name):
    suggested_name=' '.join(str(suggested_name or '').strip().split())
    empty={'suggested_name':None,'status':'none','auto_select':False,'match':None,'matches':[],'can_create':False}
    if not suggested_name:
        return empty
    rows=[dict(r) for r in c.execute('SELECT id,name FROM locations WHERE merchant=? ORDER BY name,id',(merchant,))]
    key=_name_key(suggested_name)
    exact=[r for r in rows if _name_key(r['name'])==key]
    if len(exact)==1:
        return {'suggested_name':suggested_name,'status':'exact','auto_select':True,'match':exact[0],'matches':exact,'can_create':False}
    if len(exact)>1:
        return {'suggested_name':suggested_name,'status':'ambiguous','auto_select':False,'match':None,'matches':exact,'can_create':False}
    wanted=key.split()
    token_matches=[]
    if wanted:
        for r in rows:
            tokens=_name_key(r['name']).split()
            if all(w in tokens for w in wanted):
                token_matches.append(r)
    if len(token_matches)==1:
        return {'suggested_name':suggested_name,'status':'unique_name_part','auto_select':False,'match':token_matches[0],'matches':token_matches,'can_create':False}
    if len(token_matches)>1:
        return {'suggested_name':suggested_name,'status':'ambiguous','auto_select':False,'match':None,'matches':token_matches[:8],'can_create':False}
    from difflib import SequenceMatcher
    similar=[]
    for r in rows:
        score=SequenceMatcher(None,key,_name_key(r['name'])).ratio() if key else 0
        if score>=0.70:
            item=dict(r); item['score']=round(score,3); similar.append(item)
    similar.sort(key=lambda x:(-x['score'],x['name'].lower(),x['id']))
    if similar:
        return {'suggested_name':suggested_name,'status':'similar','auto_select':False,'match':None,'matches':similar[:5],'can_create':True}
    return {'suggested_name':suggested_name,'status':'new','auto_select':False,'match':None,'matches':[],'can_create':True}



MAIN_LOCATION_ID='__main__'


def require_location(c, merchant, location_id):
    location_id=str(location_id or '').strip()
    if location_id==MAIN_LOCATION_ID:
        return {'id':MAIN_LOCATION_ID,'name':merchant+'  - Main stock'}
    row=c.execute(
        'SELECT id,name FROM locations WHERE merchant=? AND id=?',
        (merchant,location_id)
    ).fetchone()
    if not row:
        raise ValueError('Choose a registered location belonging to this business.')
    return dict(row)


def _stock_move_location(row):
    return row['location_id'] or MAIN_LOCATION_ID

def location_transfer_balance(c, merchant, product, location_id):
    location_id=str(location_id or '').strip()
    direct=0
    for r in c.execute(
        "SELECT quantity,location_id FROM stock_moves WHERE merchant=? AND product=? AND status='confirmed'",
        (merchant,product)
    ):
        if _stock_move_location(r)==location_id:
            direct += int(r['quantity'] or 0)
    inbound=c.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM stock_transfers WHERE merchant=? AND product=? AND to_location=? AND status='confirmed'",
        (merchant,product,location_id)
    ).fetchone()[0]
    outbound=c.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM stock_transfers WHERE merchant=? AND product=? AND from_location=? AND status='confirmed'",
        (merchant,product,location_id)
    ).fetchone()[0]
    return direct+inbound-outbound

def location_transfer_value(c, merchant, product, location_id):
    location_id=str(location_id or '').strip()
    value=0
    complete=True
    for r in c.execute(
        "SELECT quantity,cost_total,location_id FROM stock_moves WHERE merchant=? AND product=? AND status='confirmed' ORDER BY created,rowid",
        (merchant,product)
    ):
        if _stock_move_location(r)!=location_id:
            continue
        q=int(r['quantity'] or 0)
        cost=r['cost_total']
        if cost is None:
            complete=False
        elif q>0:
            value += int(cost)
        elif q<0:
            value -= int(cost)
    inbound=c.execute(
        "SELECT COALESCE(SUM(cost_total),0) v,SUM(CASE WHEN cost_total IS NULL THEN 1 ELSE 0 END) missing "
        "FROM stock_transfers WHERE merchant=? AND product=? AND to_location=? AND status='confirmed'",
        (merchant,product,location_id)
    ).fetchone()
    outbound=c.execute(
        "SELECT COALESCE(SUM(cost_total),0) v,SUM(CASE WHEN cost_total IS NULL THEN 1 ELSE 0 END) missing "
        "FROM stock_transfers WHERE merchant=? AND product=? AND from_location=? AND status='confirmed'",
        (merchant,product,location_id)
    ).fetchone()
    if (inbound['missing'] or 0) or (outbound['missing'] or 0):
        complete=False
    value += int(inbound['v'] or 0)-int(outbound['v'] or 0)
    if location_transfer_balance(c,merchant,product,location_id)<0:
        complete=False
    return value if complete else None



def location_inventory_rows(c):
    rows=[]
    products=[dict(r) for r in c.execute('SELECT * FROM products ORDER BY merchant,name,id')]
    locations_by_merchant={}
    for p in products:
        merchant=p['merchant']
        if merchant not in locations_by_merchant:
            locations_by_merchant[merchant]=[
                {'id':MAIN_LOCATION_ID,'name':merchant+'  - Main stock'}
            ]+[dict(r) for r in c.execute(
                'SELECT id,name FROM locations WHERE merchant=? ORDER BY name,id',
                (merchant,)
            )]
        for loc in locations_by_merchant[merchant]:
            qty=location_transfer_balance(c,merchant,p['id'],loc['id'])
            value=location_transfer_value(c,merchant,p['id'],loc['id'])
            rows.append({
                'merchant':merchant,
                'location_id':loc['id'],
                'location_name':loc['name'],
                'product':p['id'],
                'product_name':p['name'],
                'unit':p['unit'],
                'quantity':qty,
                'inventory_value':value,
                'avg_unit_cost':(
                    int(round(value*1000/qty))
                    if value is not None and qty>0 else None
                )
            })
    return rows


def transfer_draft(data, actor=None):
    fields={k:str(data.get(k,'')).strip() for k in (
        'merchant','source','product','from_location','to_location'
    )}
    if not all(fields.values()) or any(len(v)>2000 for v in fields.values()):
        raise ValueError('Complete all internal-transfer fields.')
    q=quantity(data.get('quantity'))
    if fields['from_location']==fields['to_location']:
        raise ValueError('Source and destination locations must be different.')

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        check_inbox_source(c,fields['merchant'],fields['source'],'transfer')
        require_product(c,fields['merchant'],fields['product'])
        require_location(c,fields['merchant'],fields['from_location'])
        require_location(c,fields['merchant'],fields['to_location'])
        require_transfer_actor(actor,fields['from_location'],fields['to_location'],'draft',False)

        available=location_transfer_balance(
            c,fields['merchant'],fields['product'],fields['from_location']
        )
        if available<q:
            raise ValueError(
                'Insufficient stock at the selected source location. '
                f'Available: {available/1000:g}.'
            )

        old=c.execute(
            'SELECT * FROM stock_transfers WHERE merchant=? AND source=?',
            (fields['merchant'],fields['source'])
        ).fetchone()
        if old:
            if (any(old[k]!=v for k,v in fields.items()) or old['quantity']!=q):
                raise ValueError('Source ID already used with different transfer details.')
            return old['id']

        ident=str(uuid.uuid4())
        c.execute(
            'INSERT INTO stock_transfers('
            'id,merchant,source,product,quantity,from_location,to_location,status'
            ') VALUES(?,?,?,?,?,?,?,?)',
            (
                ident,fields['merchant'],fields['source'],fields['product'],q,
                fields['from_location'],fields['to_location'],'draft'
            )
        )
        claim_inbox(c,fields['source'],'transfer',ident)
        audit(c,actor,'transfer.draft','stock_transfer',ident,{
            'product':fields['product'],'quantity':q,
            'from_location':fields['from_location'],'to_location':fields['to_location']
        })
        return ident


def transfer_action(ident, action, reason='', actor=None):
    ident=str(ident or '').strip()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT * FROM stock_transfers WHERE id=?',(ident,)).fetchone()
        if not r:
            raise ValueError('Internal transfer not found.')

        if action=='confirm':
            require_transfer_actor(actor,r['from_location'],r['to_location'],'confirm',False)
            if r['status']=='confirmed':
                return
            if r['status']!='draft':
                raise ValueError('Only active transfer drafts can be confirmed.')

            available=location_transfer_balance(
                c,r['merchant'],r['product'],r['from_location']
            )
            if available<r['quantity']:
                raise ValueError(
                    'Stock at the source location changed after this draft was created. '
                    'Cancel it and create a new transfer.'
                )

            source_value=location_transfer_value(
                c,r['merchant'],r['product'],r['from_location']
            )
            if source_value is None or available<=0:
                raise ValueError(
                    'Inventory cost basis is incomplete at the source location.'
                )

            from decimal import Decimal, ROUND_HALF_UP
            transfer_cost=int(
                (Decimal(source_value)*Decimal(r['quantity'])/Decimal(available))
                .quantize(Decimal('1'),rounding=ROUND_HALF_UP)
            )
            c.execute(
                "UPDATE stock_transfers SET status='confirmed',cost_total=? WHERE id=?",
                (transfer_cost,ident)
            )

        elif action=='reverse':
            require_transfer_actor(actor,r['from_location'],r['to_location'],'reverse',r['status']=='confirmed')
            reason=' '.join(str(reason or '').strip().split())
            if not reason or len(reason)>2000:
                raise ValueError('Give a correction reason (maximum 2000 characters).')
            if r['status'] in ('cancelled','reversed'):
                return
            if r['status']=='confirmed':
                available_at_destination=location_transfer_balance(
                    c,r['merchant'],r['product'],r['to_location']
                )
                if available_at_destination<r['quantity']:
                    raise ValueError(
                        'Reversal would make destination stock negative. '
                        'Correct dependent transfers first.'
                    )
            c.execute(
                'UPDATE stock_transfers SET status=?,reason=? WHERE id=?',
                ('cancelled' if r['status']=='draft' else 'reversed',reason,ident)
            )
        else:
            raise ValueError('Unknown transfer action.')
        audit(c,actor,'transfer.'+action,'stock_transfer',ident,{
            'from_location':r['from_location'],'to_location':r['to_location'],
            'product':r['product'],'quantity':r['quantity']
        })


def stock_draft(data, actor=None):
    fields={k:str(data.get(k,'')).strip() for k in ('merchant','source','product','kind')}
    location_id=str(data.get('location_id','')).strip() or MAIN_LOCATION_ID
    supplier_id=str(data.get('supplier','')).strip()
    destination=' '.join(str(data.get('destination','')).strip().split())
    if not all(fields.values()) or any(len(v)>2000 for v in fields.values()):
        raise ValueError('Complete all stock fields.')
    allowed=('opening','received','distributed','returned_in','returned_supplier','damaged','expired','lost','physical_count')
    if fields['kind'] not in allowed:
        raise ValueError('Choose a supported inventory movement.')

    q_input=quantity(data.get('quantity'))
    q=q_input
    cost=None
    counted_quantity=None
    baseline_quantity=None

    if fields['kind'] in ('opening','received'):
        cost=money(data.get('total_cost',''))
        if cost<=0:
            raise ValueError('Enter the total purchase cost for this stock movement.')
    elif fields['kind']=='physical_count':
        counted_quantity=q_input
    elif fields['kind'] in ('distributed','returned_supplier','damaged','expired','lost'):
        q=-q_input

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        check_inbox_source(c,fields['merchant'],fields['source'],'stock')
        require_product(c,fields['merchant'],fields['product'])
        require_location(c,fields['merchant'],location_id)
        require_stock_actor(actor,location_id,fields['kind'],'draft')

        if fields['kind']=='received':
            if not supplier_id:
                raise ValueError('Choose the supplier for stock received.')
            if not c.execute('SELECT 1 FROM suppliers WHERE merchant=? AND id=?',(fields['merchant'],supplier_id)).fetchone():
                raise ValueError('Choose a supplier belonging to this business.')
            destination=''
        elif fields['kind']=='returned_supplier':
            if not supplier_id:
                raise ValueError('Choose the supplier receiving the returned stock.')
            if not c.execute('SELECT 1 FROM suppliers WHERE merchant=? AND id=?',(fields['merchant'],supplier_id)).fetchone():
                raise ValueError('Choose a supplier belonging to this business.')
            destination=''
        elif fields['kind'] in ('distributed','returned_in'):
            if not destination or len(destination)>200:
                label='origin' if fields['kind']=='returned_in' else 'destination'
                raise ValueError(f'Enter the {label} (maximum 200 characters).')
            location_resolution=resolve_location(c,fields['merchant'],destination)
            if location_resolution.get('status')=='exact' and location_resolution.get('match'):
                destination=location_resolution['match']['name']
            else:
                label='origin' if fields['kind']=='returned_in' else 'destination'
                matches=location_resolution.get('matches') or []
                if matches:
                    names=', '.join(m['name'] for m in matches[:5])
                    raise ValueError(f'Choose a registered {label}. Possible matches: {names}.')
                raise ValueError(f'Choose a registered {label} or add it under Locations first.')
            supplier_id=''
        else:
            supplier_id=''
            destination=''

        if fields['kind']=='physical_count':
            baseline_quantity=location_transfer_balance(c,fields['merchant'],fields['product'],location_id)
            q=counted_quantity-baseline_quantity
            if q==0:
                raise ValueError('Physical count matches the current stock. No adjustment is required.')

        old=c.execute('SELECT * FROM stock_moves WHERE merchant=? AND source=?',(fields['merchant'],fields['source'])).fetchone()
        if old:
            expected_supplier=supplier_id or None
            expected_destination=destination or None
            if (any(old[k]!=v for k,v in fields.items()) or old['quantity']!=q or
                (old['location_id'] or MAIN_LOCATION_ID)!=location_id or
                old['cost_total']!=cost or (old['supplier'] or None)!=expected_supplier or
                (old['destination'] or None)!=expected_destination or
                (old['counted_quantity'] or None)!=(counted_quantity or None) or
                (old['baseline_quantity'] or None)!=(baseline_quantity or None)):
                raise ValueError('Source ID already used with different stock details.')
            return old['id']

        ident=str(uuid.uuid4())
        c.execute(
            'INSERT INTO stock_moves(id,merchant,source,product,quantity,kind,status,cost_total,supplier,destination,counted_quantity,baseline_quantity,location_id) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident,fields['merchant'],fields['source'],fields['product'],q,fields['kind'],'draft',cost,supplier_id or None,destination or None,counted_quantity,baseline_quantity,location_id)
        )
        claim_inbox(c,fields['source'],'stock',ident)
        audit(c,actor,'stock.draft','stock_move',ident,{
            'kind':fields['kind'],'location_id':location_id,'product':fields['product'],
            'quantity':q,'supplier':supplier_id or None
        })
        return ident


def stock_action(ident,action,reason='',actor=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT * FROM stock_moves WHERE id=?',(ident,)).fetchone()
        if not r:
            raise ValueError('Stock movement not found.')
        if r['entry_id']:
            raise ValueError('Confirm or reverse the linked sale in the transaction list.')

        location_id=r['location_id'] or MAIN_LOCATION_ID

        if action=='confirm':
            require_stock_actor(actor,location_id,r['kind'],'confirm')
            if r['status']=='confirmed':
                return
            if r['status']!='draft':
                raise ValueError('Only active drafts can be confirmed.')
            if r['kind']=='opening' and (
                location_transfer_balance(c,r['merchant'],r['product'],location_id)!=0
            ):
                raise ValueError('Opening stock can only be confirmed when the selected location has no existing stock. Use stock received for replenishment.')

            if r['kind']=='physical_count':
                current=location_transfer_balance(c,r['merchant'],r['product'],location_id)
                if current!=r['baseline_quantity']:
                    raise ValueError('Stock changed after this physical-count draft was created. Cancel it and review a new count.')

            if r['kind'] not in ('opening','received'):
                current_stock=location_transfer_balance(c,r['merchant'],r['product'],location_id)
                if current_stock+r['quantity']<0:
                    loc=require_location(c,r['merchant'],location_id)
                    raise ValueError(f"Insufficient stock at {loc['name']} for this movement.")
                location_value=location_transfer_value(c,r['merchant'],r['product'],location_id)
                if location_value is None or current_stock<=0:
                    raise ValueError('Inventory cost basis is incomplete at the selected location.')
                from decimal import Decimal, ROUND_HALF_UP
                move_cost=int(
                    (Decimal(location_value)*Decimal(abs(r['quantity']))/Decimal(current_stock)).quantize(
                        Decimal('1'),rounding=ROUND_HALF_UP
                    )
                )
                c.execute("UPDATE stock_moves SET status='confirmed',cost_total=? WHERE id=?",(move_cost,ident))
            else:
                c.execute("UPDATE stock_moves SET status='confirmed' WHERE id=?",(ident,))

        elif action=='reverse':
            require_stock_actor(actor,location_id,r['kind'],'reverse_confirmed' if r['status']=='confirmed' else 'reverse_draft')
            reason=str(reason or '').strip()
            if not reason or len(reason)>2000:
                raise ValueError('Give a correction reason (maximum 2000 characters).')
            if r['status'] in ('cancelled','reversed'):
                return
            if r['status']=='confirmed' and location_transfer_balance(c,r['merchant'],r['product'],location_id)-r['quantity']<0:
                raise ValueError('Reversal would make stock negative at this location. Correct dependent sales or inventory movements first.')
            c.execute(
                'UPDATE stock_moves SET status=?,reason=? WHERE id=?',
                ('cancelled' if r['status']=='draft' else 'reversed',reason,ident)
            )
        else:
            raise ValueError('Unknown stock action.')
        audit(c,actor,'stock.'+action,'stock_move',ident,{
            'kind':r['kind'],'location_id':location_id,'product':r['product'],
            'quantity':r['quantity']
        })

def _period_report_snapshot(merchant,start,end,actor=None):
    from datetime import timezone

    merchant=str(merchant or '').strip()
    if not merchant or len(merchant)>2000:
        raise ValueError('Select a shop.')

    bounds=[t.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') for t in (start,end)]

    with connect() as c:
        rows=[dict(r) for r in c.execute(
            "SELECT e.*,COALESCE(c.name,e.customer) customer_name,"
            "m.product,-m.quantity quantity,m.cost_total cogs,"
            "COALESCE(l.name,CASE WHEN COALESCE(e.location_id,m.location_id)=? THEN e.merchant || ' - Main stock' ELSE COALESCE(e.location_id,m.location_id) END) location_name,"
            "u.name created_by_name "
            "FROM entries e "
            "LEFT JOIN customers c ON c.merchant=e.merchant AND c.id=e.customer "
            "LEFT JOIN stock_moves m ON m.entry_id=e.id "
            "LEFT JOIN locations l ON l.merchant=e.merchant AND l.id=COALESCE(e.location_id,m.location_id) "
            "LEFT JOIN users u ON u.merchant=e.merchant AND u.id=e.created_by "
            "WHERE e.merchant=? AND e.status='confirmed' "
            "AND e.confirmed>=? AND e.confirmed<? "
            "ORDER BY e.confirmed,e.rowid",
            (MAIN_LOCATION_ID,merchant,*bounds)
        )]

        if actor and actor['role'] in ('branch_manager','cashier','inventory_officer'):
            allowed=actor_location_ids(actor)
            rows=[r for r in rows if (r.get('location_id') or MAIN_LOCATION_ID) in allowed]

        corrections=[dict(r) for r in c.execute(
            "SELECT e.id,e.kind,e.customer,e.description,e.total,e.paid,e.status,"
            "COALESCE(e.location_id,?) location_id,"
            "r.reason,r.created correction_created "
            "FROM entries e "
            "JOIN reversals r ON r.entry_id=e.id "
            "WHERE e.merchant=? "
            "AND r.created>=? AND r.created<? "
            "AND e.status IN ('cancelled','reversed') "
            "ORDER BY r.created DESC",
            (MAIN_LOCATION_ID,merchant,*bounds)
        )]

        if actor and actor['role'] in ('branch_manager','cashier','inventory_officer'):
            allowed=actor_location_ids(actor)
            corrections=[
                r for r in corrections
                if (r.get('location_id') or MAIN_LOCATION_ID) in allowed
            ]

        for item in corrections:
            event=c.execute(
                "SELECT user_id,role,action,created "
                "FROM audit_log "
                "WHERE merchant=? AND target_type='entry' AND target_id=? "
                "AND action IN ('entry.cancel','entry.reverse') "
                "ORDER BY created DESC,rowid DESC LIMIT 1",
                (merchant,item['id'])
            ).fetchone()

            if event:
                item['action_user_id']=event['user_id']
                item['action_role']=event['role']
                item['audit_action']=event['action']
                item['audit_created']=event['created']

                user_row=c.execute(
                    'SELECT name FROM users WHERE merchant=? AND id=?',
                    (merchant,event['user_id'])
                ).fetchone()

                item['action_user_name']=(
                    user_row['name']
                    if user_row
                    else event['user_id']
                )
            else:
                item['action_user_id']=None
                item['action_role']=None
                item['audit_action']=None
                item['audit_created']=None
                item['action_user_name']='Unknown / legacy user'

        stock_corrections=[dict(r) for r in c.execute(
            "SELECT a.created audit_created,a.action,a.user_id,a.role,"
            "m.id,m.product,m.quantity,m.kind,m.status,m.reason,"
            "COALESCE(m.location_id,?) location_id,"
            "u.name action_user_name "
            "FROM audit_log a "
            "JOIN stock_moves m ON m.id=a.target_id AND m.merchant=a.merchant "
            "LEFT JOIN users u ON u.merchant=a.merchant AND u.id=a.user_id "
            "WHERE a.merchant=? "
            "AND a.target_type='stock_move' "
            "AND a.action IN ('stock.cancel','stock.reverse') "
            "AND a.created>=? AND a.created<? "
            "ORDER BY a.created DESC",
            (MAIN_LOCATION_ID,merchant,*bounds)
        )]

        transfer_corrections=[dict(r) for r in c.execute(
            "SELECT a.created audit_created,a.action,a.user_id,a.role,"
            "t.id,t.product,t.quantity,t.from_location,t.to_location,"
            "t.status,t.reason,"
            "u.name action_user_name,"
            "lf.name from_location_name,"
            "lt.name to_location_name "
            "FROM audit_log a "
            "JOIN stock_transfers t ON t.id=a.target_id AND t.merchant=a.merchant "
            "LEFT JOIN users u ON u.merchant=a.merchant AND u.id=a.user_id "
            "LEFT JOIN locations lf ON lf.merchant=t.merchant AND lf.id=t.from_location "
            "LEFT JOIN locations lt ON lt.merchant=t.merchant AND lt.id=t.to_location "
            "WHERE a.merchant=? "
            "AND a.target_type='stock_transfer' "
            "AND a.action IN ('transfer.cancel','transfer.reverse') "
            "AND a.created>=? AND a.created<? "
            "ORDER BY a.created DESC",
            (merchant,*bounds)
        )]

        if actor and actor['role'] in ('branch_manager','cashier','inventory_officer'):
            allowed=actor_location_ids(actor)

            stock_corrections=[
                r for r in stock_corrections
                if (r.get('location_id') or MAIN_LOCATION_ID) in allowed
            ]

            transfer_corrections=[
                r for r in transfer_corrections
                if r.get('from_location') in allowed
                and r.get('to_location') in allowed
            ]

        cancelled_stock_movements=[
            r for r in stock_corrections
            if r.get('action')=='stock.cancel'
        ]

        reversed_stock_movements=[
            r for r in stock_corrections
            if r.get('action')=='stock.reverse'
        ]

        cancelled_transfers=[
            r for r in transfer_corrections
            if r.get('action')=='transfer.cancel'
        ]

        reversed_transfers=[
            r for r in transfer_corrections
            if r.get('action')=='transfer.reverse'
        ]

        security_access_summary=None
        security_user_events=[]
        security_auth_events=[]

        if actor and actor['role'] in AUTH_MANAGE_ROLES:
            security_user_events=[dict(r) for r in c.execute(
                "SELECT a.created,a.action,a.user_id actor_user_id,"
                "a.role actor_role,a.target_id,a.details,"
                "actor_user.name actor_name,"
                "target_user.name target_user_name,"
                "target_user.username target_username "
                "FROM audit_log a "
                "LEFT JOIN users actor_user "
                "ON actor_user.merchant=a.merchant AND actor_user.id=a.user_id "
                "LEFT JOIN users target_user "
                "ON target_user.merchant=a.merchant AND target_user.id=a.target_id "
                "WHERE a.merchant=? "
                "AND a.action LIKE 'user.%' "
                "AND a.created>=? AND a.created<? "
                "ORDER BY a.created DESC,a.rowid DESC",
                (merchant,*bounds)
            )]

            security_auth_events=[dict(r) for r in c.execute(
                "SELECT created,action,user_id,username,details "
                "FROM auth_events "
                "WHERE merchant=? "
                "AND action LIKE 'auth.%' "
                "AND action<>'auth.password_reset' "
                "AND created>=? AND created<? "
                "ORDER BY created DESC,rowid DESC",
                (merchant,*bounds)
            )]

            security_access_summary={
                'user_change_count':len(security_user_events),
                'user_created_count':sum(
                    1 for r in security_user_events
                    if r.get('action')=='user.create'
                ),
                'user_updated_count':sum(
                    1 for r in security_user_events
                    if r.get('action')=='user.update'
                ),
                'user_deactivated_count':sum(
                    1 for r in security_user_events
                    if r.get('action')=='user.deactivate'
                ),
                'user_reactivated_count':sum(
                    1 for r in security_user_events
                    if r.get('action')=='user.reactivate'
                ),
                'password_reset_count':sum(
                    1 for r in security_user_events
                    if r.get('action')=='user.password_reset'
                ),
                'login_success_count':sum(
                    1 for r in security_auth_events
                    if r.get('action')=='auth.login'
                ),
                'login_failed_count':sum(
                    1 for r in security_auth_events
                    if r.get('action')=='auth.login_failed'
                ),
                'login_blocked_count':sum(
                    1 for r in security_auth_events
                    if r.get('action')=='auth.login_blocked'
                ),
                'logout_count':sum(
                    1 for r in security_auth_events
                    if r.get('action')=='auth.logout'
                ),
                'password_change_count':sum(
                    1 for r in security_auth_events
                    if r.get('action')=='auth.password_change'
                ),
                'password_change_failed_count':sum(
                    1 for r in security_auth_events
                    if r.get('action')=='auth.password_change_failed'
                )
            }

        debt_rows=[dict(r) for r in c.execute(
            "SELECT e.customer,COALESCE(c.name,e.customer) customer_name,"
            "e.total,e.paid,COALESCE(e.customer_credit,0) customer_credit,e.location_id "
            "FROM entries e "
            "LEFT JOIN customers c ON c.merchant=e.merchant AND c.id=e.customer "
            "WHERE e.merchant=? AND e.status='confirmed' AND e.confirmed<?",
            (merchant,bounds[1])
        )]

        if actor and actor['role'] in ('branch_manager','cashier','inventory_officer'):
            allowed=actor_location_ids(actor)
            debt_rows=[
                r for r in debt_rows
                if (r.get('location_id') or MAIN_LOCATION_ID) in allowed
            ]

    by_customer={}
    for r in debt_rows:
        item=by_customer.setdefault(
            r['customer'],
            {
                'customer':r['customer'],
                'customer_name':r['customer_name'],
                'balance':0,
                'credit':0
            }
        )
        item['balance']+=int(r.get('total') or 0)-int(r.get('paid') or 0)+int(r.get('customer_credit') or 0)
        item['credit']+=int(r.get('customer_credit') or 0)

    debtors=sorted(
        [x for x in by_customer.values() if x['balance']>0],
        key=lambda x:x['balance'],
        reverse=True
    )

    sales=[r for r in rows if r.get('kind')=='sale']
    payments=[r for r in rows if r.get('kind')=='repayment']

    tracked_sales=[r for r in sales if r.get('product')]
    untracked_sales=[r for r in sales if not r.get('product')]
    untracked_sales_value=sum(int(r.get('total') or 0) for r in untracked_sales)

    costing_complete=all(r.get('cogs') is not None for r in tracked_sales)

    tracked_cogs=sum(int(r.get('cogs') or 0) for r in tracked_sales if r.get('cogs') is not None)
    tracked_revenue=sum(int(r.get('total') or 0) for r in tracked_sales if r.get('cogs') is not None)
    tracked_gross_profit=tracked_revenue-tracked_cogs if costing_complete else None

    priced_sales=[r for r in sales if r.get('expected_total') is not None]
    expected_sales_value=sum(int(r.get('expected_total') or 0) for r in priced_sales)
    actual_priced_sales_value=sum(int(r.get('total') or 0) for r in priced_sales)
    total_price_variance=sum(int(r.get('price_variance') or 0) for r in priced_sales)

    discount_variance=sum(
        int(r.get('price_variance') or 0)
        for r in priced_sales
        if int(r.get('price_variance') or 0)<0
    )

    markup_variance=sum(
        int(r.get('price_variance') or 0)
        for r in priced_sales
        if int(r.get('price_variance') or 0)>0
    )

    off_price_sale_count=sum(
        1 for r in priced_sales
        if int(r.get('price_variance') or 0)!=0
    )

    price_exceptions=[
        {
            'id':r.get('id'),
            'customer_name':r.get('customer_name'),
            'description':r.get('description'),
            'location_id':r.get('location_id') or MAIN_LOCATION_ID,
            'created_by':r.get('created_by'),
            'created_by_name':r.get('created_by_name') or 'Unknown / legacy user',
            'total':int(r.get('total') or 0),
            'expected_total':int(r.get('expected_total') or 0),
            'price_variance':int(r.get('price_variance') or 0),
            'pricing_source':r.get('pricing_source')
        }
        for r in priced_sales
        if int(r.get('price_variance') or 0)!=0
    ]

    incomplete_cost_sales=[
        {
            'id':r.get('id'),
            'product':r.get('product'),
            'description':r.get('description'),
            'total':int(r.get('total') or 0),
            'location_id':r.get('location_id') or MAIN_LOCATION_ID,
            'created_by_name':r.get('created_by_name') or 'Unknown / legacy user'
        }
        for r in tracked_sales
        if r.get('cogs') is None
    ]

    legacy_unattributed_sales=[
        {
            'id':r.get('id'),
            'description':r.get('description'),
            'total':int(r.get('total') or 0),
            'location_id':r.get('location_id') or MAIN_LOCATION_ID
        }
        for r in sales
        if not r.get('created_by')
    ]

    cancelled_entries=[
        r for r in corrections
        if r.get('status')=='cancelled'
    ]

    reversed_entries=[
        r for r in corrections
        if r.get('status')=='reversed'
    ]

    exception_summary={
        'price_exception_count':len(price_exceptions),
        'incomplete_cost_sale_count':len(incomplete_cost_sales),
        'untracked_sale_count':len(untracked_sales),
        'untracked_sales_value':untracked_sales_value,
        'legacy_unattributed_sale_count':len(legacy_unattributed_sales),
        'cancelled_entry_count':len(cancelled_entries),
        'reversed_entry_count':len(reversed_entries),
        'cancelled_stock_movement_count':len(cancelled_stock_movements),
        'reversed_stock_movement_count':len(reversed_stock_movements),
        'cancelled_transfer_count':len(cancelled_transfers),
        'reversed_transfer_count':len(reversed_transfers),
        'largest_negative_price_variance':(
            min(
                [
                    int(r.get('price_variance') or 0)
                    for r in price_exceptions
                    if int(r.get('price_variance') or 0)<0
                ],
                default=0
            )
        ),
        'largest_positive_price_variance':(
            max(
                [
                    int(r.get('price_variance') or 0)
                    for r in price_exceptions
                    if int(r.get('price_variance') or 0)>0
                ],
                default=0
            )
        )
    }

    gross_margin_percent=(
        tracked_gross_profit*100/tracked_revenue
        if tracked_gross_profit is not None and tracked_revenue
        else None
    )

    product_map={}
    staff_map={}
    branch_map={}

    for r in rows:
        if r.get('kind')!='sale':
            continue

        total=int(r.get('total') or 0)
        paid=int(r.get('paid') or 0)
        cogs=r.get('cogs')
        cogs_value=int(cogs) if cogs is not None else None
        variance=int(r.get('price_variance') or 0)

        product=r.get('product')
        if product:
            p=product_map.setdefault(product,{
                'product':product,
                'quantity':0,
                'revenue':0,
                'cogs':0,
                'gross_profit':0,
                'costing_complete':True,
                'price_variance':0,
                'sales_count':0
            })
            p['quantity']+=int(r.get('quantity') or 0)
            p['revenue']+=total
            p['price_variance']+=variance
            p['sales_count']+=1

            if cogs_value is None:
                p['costing_complete']=False
            else:
                p['cogs']+=cogs_value
                p['gross_profit']+=total-cogs_value

        staff_id=r.get('created_by') or '__unknown__'
        staff_name=r.get('created_by_name') or 'Unknown / legacy user'

        s=staff_map.setdefault(staff_id,{
            'user_id':staff_id,
            'staff_name':staff_name,
            'sales_count':0,
            'sales_value':0,
            'collections':0,
            'tracked_revenue':0,
            'cogs':0,
            'gross_profit':0,
            'costing_complete':True,
            'price_variance':0,
            'off_price_sales':0
        })

        s['sales_count']+=1
        s['sales_value']+=total
        s['collections']+=paid
        s['price_variance']+=variance

        if variance!=0:
            s['off_price_sales']+=1

        if product:
            if cogs_value is None:
                s['costing_complete']=False
            else:
                s['tracked_revenue']+=total
                s['cogs']+=cogs_value
                s['gross_profit']+=total-cogs_value

        location_id=r.get('location_id') or MAIN_LOCATION_ID
        location_name=(
            merchant+' - Main stock'
            if location_id==MAIN_LOCATION_ID
            else (r.get('location_name') or location_id)
        )

        b=branch_map.setdefault(location_id,{
            'location_id':location_id,
            'location_name':location_name,
            'sales_count':0,
            'sales':0,
            'collections':0,
            'tracked_revenue':0,
            'cogs':0,
            'gross_profit':0,
            'costing_complete':True,
            'price_variance':0
        })

        b['sales_count']+=1
        b['sales']+=total
        b['collections']+=paid
        b['price_variance']+=variance

        if product:
            if cogs_value is None:
                b['costing_complete']=False
            else:
                b['tracked_revenue']+=total
                b['cogs']+=cogs_value
                b['gross_profit']+=total-cogs_value

    product_performance=[]
    for p in product_map.values():
        p['gross_margin_percent']=(
            p['gross_profit']*100/p['revenue']
            if p['costing_complete'] and p['revenue']
            else None
        )
        product_performance.append(p)

    staff_performance=[]
    for s in staff_map.values():
        s['gross_margin_percent']=(
            s['gross_profit']*100/s['tracked_revenue']
            if s['costing_complete'] and s['tracked_revenue']
            else None
        )
        staff_performance.append(s)

    branch_performance=[]
    for b in branch_map.values():
        b['gross_margin_percent']=(
            b['gross_profit']*100/b['tracked_revenue']
            if b['costing_complete'] and b['tracked_revenue']
            else None
        )
        branch_performance.append(b)

    product_performance.sort(key=lambda x:x['revenue'],reverse=True)
    staff_performance.sort(key=lambda x:x['sales_value'],reverse=True)
    branch_performance.sort(key=lambda x:x['sales'],reverse=True)

    return {
        'sales':sum(int(r.get('total') or 0) for r in sales),
        'sale_receipts':sum(int(r.get('paid') or 0) for r in sales),
        'repayments':sum(int(r.get('paid') or 0) for r in payments),
        'collections':sum(int(r.get('paid') or 0) for r in rows),
        'customer_credit_received':sum(int(r.get('customer_credit') or 0) for r in payments),

        'new_debt':sum(
            int(r.get('total') or 0)-int(r.get('paid') or 0)
            for r in sales
        ),

        'current_debt':sum(int(r.get('balance') or 0) for r in debtors),

        'tracked_revenue':tracked_revenue,
        'tracked_cogs':tracked_cogs,
        'tracked_gross_profit':tracked_gross_profit,
        'costing_complete':costing_complete,
        'gross_margin_percent':gross_margin_percent,

        'expected_sales_value':expected_sales_value,
        'actual_priced_sales_value':actual_priced_sales_value,
        'total_price_variance':total_price_variance,
        'discount_variance':discount_variance,
        'markup_variance':markup_variance,

        'sale_count':len(sales),
        'repayment_count':len(payments),
        'priced_sale_count':len(priced_sales),
        'off_price_sale_count':off_price_sale_count,
        'tracked_sale_count':len(tracked_sales),
        'untracked_sale_count':len(untracked_sales),
        'untracked_sales_value':untracked_sales_value,

        'product_performance':product_performance,
        'staff_performance':staff_performance,
        'branch_performance':branch_performance,

        'exception_summary':exception_summary,
        'price_exceptions':price_exceptions,
        'incomplete_cost_sales':incomplete_cost_sales,
        'legacy_unattributed_sales':legacy_unattributed_sales,
        'cancelled_entries':cancelled_entries,
        'reversed_entries':reversed_entries,
        'cancelled_stock_movements':cancelled_stock_movements,
        'reversed_stock_movements':reversed_stock_movements,
        'cancelled_transfers':cancelled_transfers,
        'reversed_transfers':reversed_transfers,

        'security_access_summary':security_access_summary,
        'security_user_events':security_user_events,
        'security_auth_events':security_auth_events,

        'entries':rows,
        'debtors':debtors
    }


def _period_metric_comparison(current,previous):
    metrics=[
        'sales',
        'sale_receipts',
        'repayments',
        'collections',
        'new_debt',
        'current_debt',
        'tracked_revenue',
        'tracked_cogs',
        'tracked_gross_profit',
        'expected_sales_value',
        'actual_priced_sales_value',
        'total_price_variance',
        'discount_variance',
        'markup_variance',
        'sale_count',
        'repayment_count',
        'off_price_sale_count'
    ]

    result={}

    for key in metrics:
        now=current.get(key)
        before=previous.get(key)

        if now is None or before is None:
            result[key]={
                'current':now,
                'previous':before,
                'delta':None,
                'percent_change':None
            }
            continue

        delta=now-before

        if before==0:
            pct=None if now==0 else None
        else:
            pct=(delta*100)/abs(before)

        result[key]={
            'current':now,
            'previous':before,
            'delta':delta,
            'percent_change':pct
        }

    current_margin=current.get('gross_margin_percent')
    previous_margin=previous.get('gross_margin_percent')

    result['gross_margin_percent']={
        'current':current_margin,
        'previous':previous_margin,
        'delta_points':(
            current_margin-previous_margin
            if current_margin is not None and previous_margin is not None
            else None
        )
    }

    return result


def period_report(merchant,start_date,end_date,actor=None,compare=True,previous_start_date=None,previous_end_date=None):
    from datetime import date,datetime,time,timedelta,timezone

    merchant=str(merchant or '').strip()

    try:
        first=date.fromisoformat(str(start_date or ''))
        last=date.fromisoformat(str(end_date or ''))
    except (ValueError,TypeError):
        raise ValueError('Choose valid start and end dates in YYYY-MM-DD format.')

    if last<first:
        raise ValueError('End date cannot be earlier than start date.')

    days=(last-first).days+1

    if days>3660:
        raise ValueError('The selected reporting period is too large.')

    lagos_tz=timezone(timedelta(hours=1))

    start=datetime.combine(first,time(),lagos_tz)
    end=datetime.combine(last+timedelta(days=1),time(),lagos_tz)

    current=_period_report_snapshot(merchant,start,end,actor)

    result={
        'merchant':merchant,
        'start_date':first.isoformat(),
        'end_date':last.isoformat(),
        'days':days,
        'timezone':'Africa/Lagos (UTC+01:00)',
        'current':current,
        'previous':None,
        'comparison':None
    }

    if compare:
        if previous_start_date and previous_end_date:
            try:
                previous_first=date.fromisoformat(str(previous_start_date))
                previous_last=date.fromisoformat(str(previous_end_date))
            except (ValueError,TypeError):
                raise ValueError('Choose valid comparison dates in YYYY-MM-DD format.')

            if previous_last < previous_first:
                raise ValueError('Comparison end date cannot be earlier than its start date.')

            previous_start=datetime.combine(previous_first,time(),lagos_tz)
            previous_end=datetime.combine(previous_last+timedelta(days=1),time(),lagos_tz)
        else:
            previous_end=start
            previous_start=start-timedelta(days=days)

        previous=_period_report_snapshot(
            merchant,
            previous_start,
            previous_end,
            actor
        )

        result['previous_start_date']=previous_start.date().isoformat()
        result['previous_end_date']=(previous_end-timedelta(days=1)).date().isoformat()
        result['previous']=previous
        result['comparison']=_period_metric_comparison(current,previous)

    return result


def daily_report(merchant,day,actor=None):
    report=period_report(
        merchant,
        day,
        day,
        actor,
        compare=False
    )

    result=dict(report['current'])
    result.update({
        'merchant':merchant,
        'date':day,
        'timezone':report['timezone']
    })

    return result

def add_shop(data):



    ident=str(data.get('id','')).strip();name=str(data.get('name','')).strip()



    if not ident or not name or len(ident)>200 or len(name)>200:raise ValueError('Shop code and name are required (maximum 200 characters).')



    with connect() as c:



        if c.execute('SELECT 1 FROM shops WHERE lower(id)=lower(?)',(ident,)).fetchone():raise ValueError('This shop code already exists. Choose it from the shop selector.')



        c.execute('INSERT INTO shops VALUES(?,?)',(ident,name))







def backup_database():



    import tempfile



    with tempfile.TemporaryDirectory(prefix='voice-ledger-backup-') as folder:



        target=Path(folder)/'backup.sqlite3'



        source=connect()



        try:



            dest=sqlite3.connect(target)



            try:



                source.backup(dest)



                if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed.')



            finally:dest.close()



        finally:source.close()



        return target.read_bytes()







def inbox_add(merchant,provider_id,sender,receiver,body,channel='whatsapp'):



    if not isinstance(body,str) or not body.strip() or len(body)>2000:



        raise ValueError('Text must contain 1â€“2000 characters.')



    with connect() as c:



        c.execute('BEGIN IMMEDIATE')



        if not c.execute('SELECT 1 FROM shops WHERE id=?',(merchant,)).fetchone():



            raise ValueError('Create the shop before receiving notes.')



        old=c.execute('SELECT id FROM inbox WHERE provider_id=?',(provider_id,)).fetchone()



        if old:return old['id']



        ident=str(uuid.uuid4())



        c.execute('INSERT INTO inbox(id,merchant,provider_id,sender,receiver,body,channel) VALUES(?,?,?,?,?,?,?)',



                  (ident,merchant,provider_id,sender,receiver,body,channel))



        return ident







def extract_whatsapp_text_messages(data):



    """Return normalized inbound WhatsApp text messages from a Meta webhook payload.



    Status callbacks and unsupported message types are ignored. The caller still

    acknowledges the webhook so Meta does not retry non-message events.

    """



    if not isinstance(data, dict):



        raise ValueError('Expected webhook JSON object.')



    if data.get('object') != 'whatsapp_business_account':



        return []



    normalized = []



    for entry in data.get('entry') or []:



        if not isinstance(entry, dict):



            continue



        for change in entry.get('changes') or []:



            if not isinstance(change, dict):



                continue



            value = change.get('value') or {}



            if not isinstance(value, dict):



                continue



            metadata = value.get('metadata') or {}



            if not isinstance(metadata, dict):



                metadata = {}



            receiver = str(



                metadata.get('display_phone_number')



                or metadata.get('phone_number_id')



                or ''



            ).strip()



            for message in value.get('messages') or []:



                if not isinstance(message, dict):



                    continue



                if message.get('type') != 'text':



                    continue



                text = message.get('text') or {}



                if not isinstance(text, dict):



                    continue



                body = str(text.get('body') or '').strip()



                provider_id = str(message.get('id') or '').strip()



                sender = str(message.get('from') or '').strip()



                if not body or not provider_id or not sender:



                    continue



                normalized.append({



                    'provider_id': provider_id,



                    'sender': sender,



                    'receiver': receiver or 'unknown',



                    'body': body,



                })



    return normalized









def extract_whatsapp_audio_messages(data):
    """Return normalized inbound WhatsApp audio/voice messages."""
    if not isinstance(data, dict):
        raise ValueError('Expected webhook JSON object.')
    if data.get('object') != 'whatsapp_business_account':
        return []

    normalized = []
    for entry in data.get('entry') or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get('changes') or []:
            if not isinstance(change, dict):
                continue
            value = change.get('value') or {}
            if not isinstance(value, dict):
                continue
            metadata = value.get('metadata') or {}
            if not isinstance(metadata, dict):
                metadata = {}
            receiver = str(metadata.get('display_phone_number') or metadata.get('phone_number_id') or '').strip()

            for message in value.get('messages') or []:
                if not isinstance(message, dict) or message.get('type') != 'audio':
                    continue
                audio = message.get('audio') or {}
                if not isinstance(audio, dict):
                    continue
                provider_id = str(message.get('id') or '').strip()
                sender = str(message.get('from') or '').strip()
                media_id = str(audio.get('id') or '').strip()
                mime_type = str(audio.get('mime_type') or '').strip()
                if not provider_id or not sender or not media_id:
                    continue
                normalized.append({
                    'provider_id': provider_id,
                    'sender': sender,
                    'receiver': receiver or 'unknown',
                    'media_id': media_id,
                    'mime_type': mime_type,
                })
    return normalized


def verify_whatsapp_signature(raw_body, signature_header):
    """Verify X-Hub-Signature-256 when WHATSAPP_APP_SECRET is configured."""
    if not WHATSAPP_APP_SECRET:
        return False
    if not signature_header or not signature_header.startswith('sha256='):
        return False
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode('utf-8'), raw_body, hashlib.sha256
    ).hexdigest()
    supplied = signature_header[7:].strip()
    return hmac.compare_digest(expected, supplied)


def voice_job_add(merchant, provider_id, sender, receiver, media_id, mime_type=''):
    """Persist an audio job quickly so the webhook can acknowledge Meta immediately."""
    values = [str(v or '').strip() for v in (merchant, provider_id, sender, receiver, media_id)]
    if not all(values) or any(len(v) > 2000 for v in values):
        raise ValueError('Invalid WhatsApp audio message.')
    merchant, provider_id, sender, receiver, media_id = values
    mime_type = str(mime_type or '').strip()[:200]

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM shops WHERE id=?', (merchant,)).fetchone():
            raise ValueError('Create the shop before receiving notes.')
        old = c.execute('SELECT id,status FROM voice_jobs WHERE provider_id=?', (provider_id,)).fetchone()
        if old:
            return old['id'], False
        ident = str(uuid.uuid4())
        c.execute(
            'INSERT INTO voice_jobs(id,merchant,provider_id,sender,receiver,media_id,mime_type,status) '
            'VALUES(?,?,?,?,?,?,?,?)',
            (ident, merchant, provider_id, sender, receiver, media_id, mime_type, 'pending')
        )
        return ident, True


def _meta_json(path):
    if not WHATSAPP_ACCESS_TOKEN:
        raise ValueError('WHATSAPP_ACCESS_TOKEN is not configured.')
    url = 'https://graph.facebook.com/' + WHATSAPP_GRAPH_VERSION + '/' + path.lstrip('/')
    req = Request(url, headers={'Authorization': 'Bearer ' + WHATSAPP_ACCESS_TOKEN})
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read(1024 * 1024)
    except HTTPError as e:
        detail = e.read(2000).decode('utf-8', 'replace')
        raise ValueError('Meta API error ' + str(e.code) + ': ' + detail)
    except URLError as e:
        raise ValueError('Could not reach Meta API: ' + str(e.reason))
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('Meta API returned an unexpected response.')
    return data


def _download_whatsapp_media(media_id):
    metadata = _meta_json(media_id)
    media_url = str(metadata.get('url') or '').strip()
    if not media_url:
        raise ValueError('Meta did not return a media download URL.')

    req = Request(media_url, headers={'Authorization': 'Bearer ' + WHATSAPP_ACCESS_TOKEN})
    try:
        with urlopen(req, timeout=30) as response:
            declared = response.headers.get('Content-Length')
            if declared and int(declared) > WHATSAPP_MEDIA_MAX_BYTES:
                raise ValueError('WhatsApp audio exceeds the configured size limit.')
            raw = response.read(WHATSAPP_MEDIA_MAX_BYTES + 1)
    except HTTPError as e:
        detail = e.read(2000).decode('utf-8', 'replace')
        raise ValueError('Meta media download error ' + str(e.code) + ': ' + detail)
    except URLError as e:
        raise ValueError('Could not download WhatsApp media: ' + str(e.reason))

    if len(raw) > WHATSAPP_MEDIA_MAX_BYTES:
        raise ValueError('WhatsApp audio exceeds the configured size limit.')
    if not raw:
        raise ValueError('Downloaded WhatsApp audio was empty.')
    return raw, str(metadata.get('mime_type') or '').strip()


def transcription_context(merchant):
    """Build conservative shop-specific hints for customer/product name recognition."""
    merchant = str(merchant or '').strip()
    generic = [
        'naira', 'cash', 'credit', 'debt', 'repayment', 'paid', 'received',
        'sale', 'sold', 'customer', 'balance', 'advance', 'bag', 'bags',
        'carton', 'cartons', 'piece', 'pieces', 'unit', 'units', 'supplier',
        'supplied', 'delivered', 'stock', 'distributed', 'issued', 'sent',
        'branch', 'store', 'warehouse'
    ]
    customers = []
    products = []
    suppliers = []
    if merchant:
        with connect() as c:
            customers = [r['name'] for r in c.execute(
                'SELECT name FROM customers WHERE merchant=? AND id<>? ORDER BY name LIMIT 40',
                (merchant, WALK_IN_CUSTOMER_ID)
            )]
            products = [r['name'] for r in c.execute(
                'SELECT name FROM products WHERE merchant=? ORDER BY name LIMIT 40',
                (merchant,)
            )]
            suppliers = [r['name'] for r in c.execute(
                'SELECT name FROM suppliers WHERE merchant=? ORDER BY name LIMIT 30',
                (merchant,)
            )]

    keywords = []
    seen = set()
    for value in customers + products + suppliers + generic:
        clean = ' '.join(str(value or '').replace('\r',' ').replace('\n',' ').split()).strip()
        key = clean.casefold()
        if clean and key not in seen and '<' not in clean and '>' not in clean:
            keywords.append(clean[:100])
            seen.add(key)
        if len(keywords) >= 60:
            break

    customer_preview = ', '.join(customers[:12])
    product_preview = ', '.join(products[:12])
    supplier_preview = ', '.join(suppliers[:10])
    parts = [f'Nigerian small-business bookkeeping for {merchant}.' if merchant else 'Nigerian small-business bookkeeping.']
    if customer_preview:
        parts.append('Known customer names include: ' + customer_preview + '.')
    if product_preview:
        parts.append('Known product names include: ' + product_preview + '.')
    if supplier_preview:
        parts.append('Known supplier names include: ' + supplier_preview + '.')
    parts.append('Amounts may be spoken in naira and names should be preserved exactly when heard. Stock may be received from suppliers or distributed to branches, stores, warehouses or other destinations.')
    return {
        'prompt': ' '.join(parts)[:1200],
        'keywords': keywords,
        'languages': ['en'],
    }



def _queue_voice_review_acknowledgement(job, inbox_id):
    """Queue a review-only reply after successful transcription.

    The outbound queue deduplicates using the original provider key.
    No ledger confirmation or inventory mutation occurs here.
    """
    from whatsapp_responses import format_response
    import whatsapp_outbound

    review = inbox_parse(inbox_id)
    reply_text = format_response(review)

    whatsapp_outbound.enqueue(
        job['provider_id'],
        job['sender'],
        job['receiver'],
        reply_text
    )

def process_voice_job(job_id):
    """Download, transcribe, and feed one voice note into the normal review inbox."""
    job_id = str(job_id or '').strip()
    if not job_id:
        raise ValueError('Voice job ID is required.')

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM voice_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Voice job not found.')
        if row['status'] == 'transcribed':
            return row['inbox_id']
        if row['status'] == 'processing':
            return row['inbox_id']
        c.execute(
            "UPDATE voice_jobs SET status='processing',error=NULL,updated=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,)
        )
        job = dict(row)

    try:
        raw, downloaded_mime = _download_whatsapp_media(job['media_id'])
        ctx = transcription_context(job['merchant'])
        result = voice.transcribe(
            raw,
            downloaded_mime or job['mime_type'],
            prompt=ctx['prompt'],
            keywords=ctx['keywords'],
            languages=ctx['languages'],
        )
        if not isinstance(result, dict):
            raise ValueError('Transcriber returned an unexpected result.')
        transcript = str(result.get('text') or '').strip()
        if not transcript:
            raise ValueError('Voice note transcription was empty.')
        if len(transcript) > 2000:
            raise ValueError('Voice note transcription exceeds 2000 characters.')

        inbox_id = inbox_add(
            job['merchant'], job['provider_id'], job['sender'], job['receiver'],
            transcript, 'whatsapp-voice'
        )
        with connect() as c:
            c.execute(
                "UPDATE voice_jobs SET status='transcribed',transcript=?,inbox_id=?,mime_type=?,error=NULL,updated=CURRENT_TIMESTAMP WHERE id=?",
                (transcript, inbox_id, downloaded_mime or job['mime_type'], job_id)
            )

        # Voice transcription is committed before we attempt an acknowledgement.
        # A reply failure must not invalidate an otherwise successful transcript.
        try:
            _queue_voice_review_acknowledgement(job, inbox_id)
        except Exception as reply_error:
            print(
                "WHATSAPP VOICE ACKNOWLEDGEMENT ERROR:",
                type(reply_error).__name__,
                flush=True
            )

        print('WHATSAPP VOICE TRANSCRIBED:', json.dumps({'job_id': job_id, 'inbox_id': inbox_id, 'text': transcript}, ensure_ascii=False), flush=True)
        return inbox_id
    except Exception as e:
        message = str(e)[:2000]
        with connect() as c:
            c.execute(
                "UPDATE voice_jobs SET status='error',error=?,updated=CURRENT_TIMESTAMP WHERE id=?",
                (message, job_id)
            )
        print('WHATSAPP VOICE ERROR:', json.dumps({'job_id': job_id, 'error': message}, ensure_ascii=False), flush=True)
        raise


def process_voice_job_background(job_id):
    try:
        process_voice_job(job_id)
    except Exception:
        pass


def start_voice_job(job_id):
    threading.Thread(target=process_voice_job_background, args=(job_id,), daemon=True).start()



def recover_voice_acknowledgements():
    """Queue missing voice review replies; never resend existing outbox rows.

    An existing outbox row, regardless of state, is deliberately left alone:
    an uncertain delivery must not be retried automatically.
    """
    with connect() as c:
        rows = [
            dict(row) for row in c.execute("""
                SELECT v.provider_id, v.sender, v.receiver, v.inbox_id
                FROM voice_jobs AS v
                WHERE v.status='transcribed'
                  AND v.inbox_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM whatsapp_reply_outbox AS o
                      WHERE o.provider_key=v.provider_id
                  )
                ORDER BY v.updated, v.id
            """)
        ]

    # Historical backlog is preserved but excluded from automatic replies.
    # If the baseline is unavailable, fail closed rather than send old replies.
    import json
    from pathlib import Path

    baseline_path = Path(__file__).with_name(
        'm9a97_historical_recovery_baseline.json'
    )

    if not baseline_path.exists():
        raise RuntimeError(
            'Historical recovery baseline missing; automatic recovery disabled.'
        )

    baseline = json.loads(
        baseline_path.read_text(encoding='utf-8')
    )

    historical_jobs = baseline.get('historical_jobs')

    if (
        not isinstance(historical_jobs, list)
        or baseline.get('missing_acknowledgement_count')
            != len(historical_jobs)
    ):
        raise RuntimeError(
            'Historical recovery baseline invalid; automatic recovery disabled.'
        )

    excluded = {
        item['provider_id']
        for item in historical_jobs
    }

    if len(excluded) != len(historical_jobs):
        raise RuntimeError(
            'Historical recovery baseline contains duplicate identifiers.'
        )

    recovered = 0
    failed = 0

    for job in rows:
        if job['provider_id'] in excluded:
            continue

        try:
            _queue_voice_review_acknowledgement(
                job, job['inbox_id']
            )
            recovered += 1
        except Exception as exc:
            failed += 1
            print(
                "WHATSAPP VOICE RECOVERY ERROR:",
                type(exc).__name__,
                flush=True
            )

    print(
        "WHATSAPP VOICE RECOVERY:",
        "queued=" + str(recovered),
        "errors=" + str(failed),
        flush=True
    )
    return {'queued': recovered, 'errors': failed}


def resume_pending_voice_jobs():
    """Recover jobs interrupted by a previous local server shutdown."""
    with connect() as c:
        c.execute("UPDATE voice_jobs SET status='pending',updated=CURRENT_TIMESTAMP WHERE status='processing'")
        pending = [r['id'] for r in c.execute("SELECT id FROM voice_jobs WHERE status='pending' ORDER BY created,rowid")]
    for ident in pending:
        start_voice_job(ident)



def _voice_price_clarification(c, inbox_row, suggestion, product_match):
    """Advisory only: compare a voice sale against its merchant price."""
    from decimal import Decimal, InvalidOperation

    if inbox_row['channel'] != 'whatsapp-voice':
        return None
    if not isinstance(suggestion, dict) or suggestion.get('kind') != 'sale':
        return None
    if not isinstance(product_match, dict):
        return None

    try:
        qty = Decimal(str(suggestion.get('quantity')))
        amount = Decimal(str(suggestion.get('total')))
        if not qty.is_finite() or not amount.is_finite():
            return None
        if qty <= 0 or amount <= 0:
            return None
        if qty.as_tuple().exponent < -3:
            return None
        qty_units = int(qty * 1000)
        total_kobo_decimal = amount * 100
        total_kobo = int(total_kobo_decimal)
        if Decimal(total_kobo) != total_kobo_decimal or qty_units <= 0:
            return None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None

    merchant = inbox_row['merchant']
    product = product_match['id']

    # Location must be unambiguous. Never borrow another branch's price.
    locations = c.execute(
        'SELECT id FROM locations WHERE merchant=? LIMIT 2',
        (merchant,)
    ).fetchall()

    branch_unspecified = len(locations) != 1

    if branch_unspecified:
        # No location has been identified. Use only this merchant's
        # company base price as a reference, never as a selected branch.
        base = c.execute(
            'SELECT base_selling_price FROM products WHERE merchant=? AND id=?',
            (merchant, product)
        ).fetchone()

        if not base or base['base_selling_price'] is None:
            return None

        from decimal import ROUND_HALF_UP

        expected = int(
            (Decimal(base['base_selling_price']) *
             Decimal(qty_units) / Decimal(1000)).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP
            )
        )
    else:
        location_id = locations[0]['id']

        pricing = _sale_price_context(
            c, None, merchant, product, location_id,
            qty_units, total_kobo
        )

        if not pricing:
            return None

        expected = pricing['expected_total']

    difference = abs(total_kobo - expected)

    # Material deviation: at least 20% and at least NGN 1,000.
    if expected <= 0 or difference < 100000:
        return None
    if Decimal(difference) / Decimal(expected) < Decimal('0.20'):
        return None

    return {
        'heard_total': total_kobo,
        'expected_total': expected,
        'difference': total_kobo - expected,
        'requires_clarification': True,
        'branch_unspecified': branch_unspecified,
    }

def inbox_parse(inbox_id, selected_kind=None):

    """Parse one stored inbox note into a review-only suggestion.



    This does not create a ledger draft and does not claim the inbox note.

    """

    inbox_id = str(inbox_id or '').strip()

    if not inbox_id or len(inbox_id) > 200:

        raise ValueError('Choose a valid inbox note.')



    with connect() as c:

        row = c.execute(

            'SELECT i.*, c.target_kind, c.target_id '

            'FROM inbox i LEFT JOIN inbox_claims c ON c.inbox_id=i.id '

            'WHERE i.id=?',

            (inbox_id,)

        ).fetchone()



        if row is None:

            raise ValueError('Inbox note not found.')



        parsed = parse_for_review(row['merchant'], row['body'], selected_kind)



        product_match = None

        suggested_product = str(parsed.get('product') or '').strip()

        if suggested_product:

            matches = c.execute(

                'SELECT id,name,unit FROM products '

                'WHERE merchant=? AND lower(name)=lower(?)',

                (row['merchant'], suggested_product)

            ).fetchall()

            if len(matches) == 1:

                product_match = dict(matches[0])



        price_clarification = _voice_price_clarification(
            c, row, parsed, product_match
        )

        return {

            'inbox': {

                'id': row['id'],

                'merchant': row['merchant'],

                'provider_id': row['provider_id'],

                'sender': row['sender'],

                'receiver': row['receiver'],

                'body': row['body'],

                'channel': row['channel'],

                'created': row['created'],

                'target_kind': row['target_kind'],

                'target_id': row['target_id'],

            },

            'source': 'inbox:' + row['id'],

            'suggestion': parsed,

            'customer_resolution': parsed.get('customer_resolution'),
            'supplier_resolution': parsed.get('supplier_resolution'),
            'location_resolution': parsed.get('location_resolution'),

            'product_match': product_match,
            'price_clarification': price_clarification,

            'notice': 'Review only. No ledger draft has been created or confirmed.'

        }





def check_inbox_source(c,merchant,source,kind):



    if not source.startswith('inbox:'):return



    ident=source[6:]



    row=c.execute('SELECT merchant FROM inbox WHERE id=?',(ident,)).fetchone()



    if not row or row['merchant']!=merchant:raise ValueError('Inbox note does not belong to this shop.')



    claim=c.execute('SELECT target_kind FROM inbox_claims WHERE inbox_id=?',(ident,)).fetchone()



    if claim and claim['target_kind']!=kind:raise ValueError('This note already created a different draft. Review its history.')







def claim_inbox(c,source,kind,ident):



    if source.startswith('inbox:'):



        c.execute('INSERT INTO inbox_claims VALUES(?,?,?)',(source[6:],kind,ident))








RECONCILIATION_CREATE_ROLES = ('owner','admin','branch_manager')


def create_reconciliation_session(actor,data):
    if not actor:
        raise PermissionError('Authentication required.')

    if actor['role'] not in RECONCILIATION_CREATE_ROLES:
        raise PermissionError(
            'Your role cannot create a reconciliation session.'
        )

    merchant=str(data.get('merchant') or '').strip()
    if not merchant:
        raise ValueError('Business is required.')

    if merchant != actor['merchant']:
        raise PermissionError(
            'You cannot reconcile another business.'
        )

    location_id=str(data.get('location_id') or '').strip()
    if not location_id:
        raise ValueError('Choose the location to reconcile.')

    start_date=str(data.get('start_date') or '').strip()
    end_date=str(data.get('end_date') or '').strip()

    from datetime import date,datetime,time,timedelta,timezone

    try:
        first=date.fromisoformat(start_date)
        last=date.fromisoformat(end_date)
    except (ValueError,TypeError):
        raise ValueError(
            'Choose valid start and end dates in YYYY-MM-DD format.'
        )

    if last < first:
        raise ValueError(
            'End date cannot be earlier than start date.'
        )

    if (last-first).days > 366:
        raise ValueError(
            'A reconciliation period cannot exceed 367 days.'
        )

    lagos_tz=timezone(timedelta(hours=1))

    start=datetime.combine(first,time(),lagos_tz)
    end=datetime.combine(
        last+timedelta(days=1),
        time(),
        lagos_tz
    )

    bounds=[
        t.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for t in (start,end)
    ]

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')

        require_location(c,merchant,location_id)

        if actor['role']=='branch_manager':
            if not actor_can_use_location(actor,location_id):
                raise PermissionError(
                    'This account is not assigned to the selected reconciliation location.'
                )

        existing=c.execute(
            "SELECT id,status "
            "FROM reconciliation_sessions "
            "WHERE merchant=? "
            "AND location_id=? "
            "AND period_start=? "
            "AND period_end=? "
            "AND status<>'approved' "
            "ORDER BY created DESC "
            "LIMIT 1",
            (
                merchant,
                location_id,
                first.isoformat(),
                last.isoformat()
            )
        ).fetchone()

        if existing:
            raise ValueError(
                'A reconciliation session already exists for this location and exact period.'
            )

        expected=int(c.execute(
            "SELECT COALESCE(SUM(paid),0) "
            "FROM entries "
            "WHERE merchant=? "
            "AND status='confirmed' "
            "AND COALESCE(location_id,?)=? "
            "AND confirmed>=? "
            "AND confirmed<?",
            (
                merchant,
                MAIN_LOCATION_ID,
                location_id,
                *bounds
            )
        ).fetchone()[0] or 0)

        presented=int(c.execute(
            "SELECT COALESCE(SUM(amount),0) "
            "FROM payment_records "
            "WHERE merchant=? "
            "AND location_id=? "
            "AND payment_date>=? "
            "AND payment_date<=? "
            "AND status<>'void'",
            (
                merchant,
                location_id,
                first.isoformat(),
                last.isoformat()
            )
        ).fetchone()[0] or 0)

        matched=0
        variance=presented-expected

        if variance==0:
            amount_status='balanced'
        elif variance<0:
            amount_status='shortfall'
        else:
            amount_status='overage'

        import uuid
        ident=str(uuid.uuid4())

        c.execute(
            "INSERT INTO reconciliation_sessions("
            "id,merchant,location_id,period_start,period_end,"
            "expected_collections,presented_collections,"
            "matched_amount,variance,status,created_by"
            ") VALUES(?,?,?,?,?,?,?,?,?,'open',?)",
            (
                ident,
                merchant,
                location_id,
                first.isoformat(),
                last.isoformat(),
                expected,
                presented,
                matched,
                variance,
                actor['id']
            )
        )

        location=c.execute(
            "SELECT name FROM locations "
            "WHERE merchant=? AND id=?",
            (merchant,location_id)
        ).fetchone()

        audit(
            c,
            actor,
            'reconciliation.create',
            'reconciliation_session',
            ident,
            {
                'location_id':location_id,
                'period_start':first.isoformat(),
                'period_end':last.isoformat(),
                'expected_collections':expected,
                'presented_collections':presented,
                'variance':variance
            }
        )

        return {
            'id':ident,
            'merchant':merchant,
            'location_id':location_id,
            'location_name':location['name'] if location else location_id,
            'start_date':first.isoformat(),
            'end_date':last.isoformat(),
            'timezone':'Africa/Lagos (UTC+01:00)',
            'expected_collections':expected,
            'presented_collections':presented,
            'matched_amount':matched,
            'unmatched_evidence':presented-matched,
            'unmatched_ledger':expected-matched,
            'variance':variance,
            'amount_status':amount_status,
            'status':'open'
        }


DAILY_CLOSING_SUBMIT_ROLES = ('owner','admin','branch_manager')


def submit_daily_closing(actor,data):
    if not actor:
        raise PermissionError('Authentication required.')

    if actor['role'] not in DAILY_CLOSING_SUBMIT_ROLES:
        raise PermissionError(
            'Your role cannot submit a daily closing.'
        )

    merchant=str(data.get('merchant') or '').strip()
    location_id=str(data.get('location_id') or '').strip()
    business_date=str(data.get('business_date') or '').strip()
    reconciliation_session_id=str(
        data.get('reconciliation_session_id') or ''
    ).strip()

    closing_note=' '.join(
        str(data.get('closing_note') or '').strip().split()
    )

    if not merchant:
        raise ValueError('Business is required.')

    if merchant != actor['merchant']:
        raise PermissionError(
            'You cannot submit a daily closing for another business.'
        )

    if not location_id:
        raise ValueError('Closing location is required.')

    if not business_date:
        raise ValueError('Business date is required.')

    if not reconciliation_session_id:
        raise ValueError(
            'Approved reconciliation session is required.'
        )

    if len(closing_note) > 1000:
        raise ValueError(
            'Closing note must not exceed 1000 characters.'
        )

    from datetime import date

    try:
        closing_day=date.fromisoformat(business_date)
    except Exception:
        raise ValueError('Business date must be a valid ISO date.')

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')

        location=require_location(
            c,
            merchant,
            location_id
        )

        if (
            actor['role']=='branch_manager'
            and not actor_can_use_location(actor,location_id)
        ):
            raise PermissionError(
                'This account is not assigned to the selected closing location.'
            )

        session=c.execute(
            "SELECT * FROM reconciliation_sessions "
            "WHERE id=? AND merchant=?",
            (
                reconciliation_session_id,
                merchant
            )
        ).fetchone()

        if not session:
            raise ValueError(
                'Reconciliation session was not found.'
            )

        if session['location_id'] != location_id:
            raise ValueError(
                'Reconciliation session belongs to a different location.'
            )

        if (
            session['period_start'] != closing_day.isoformat()
            or session['period_end'] != closing_day.isoformat()
        ):
            raise ValueError(
                'Daily closing requires a reconciliation session for the exact business date.'
            )

        if session['status'] != 'approved':
            raise ValueError(
                'Daily closing requires an approved reconciliation session.'
            )

        existing=c.execute(
            "SELECT id FROM daily_closings "
            "WHERE merchant=? "
            "AND location_id=? "
            "AND business_date=?",
            (
                merchant,
                location_id,
                closing_day.isoformat()
            )
        ).fetchone()

        if existing:
            raise ValueError(
                'A daily closing already exists for this location and business date.'
            )

        import uuid
        ident=str(uuid.uuid4())

        expected=int(session['expected_collections'] or 0)
        presented=int(session['presented_collections'] or 0)
        matched=int(session['matched_amount'] or 0)
        variance=int(session['variance'] or 0)

        c.execute(
            "INSERT INTO daily_closings("
            "id,merchant,location_id,business_date,"
            "reconciliation_session_id,"
            "expected_collections,presented_collections,"
            "matched_amount,variance,closing_status,"
            "closing_note,submitted_by"
            ") VALUES(?,?,?,?,?,?,?,?,?,'submitted',?,?)",
            (
                ident,
                merchant,
                location_id,
                closing_day.isoformat(),
                reconciliation_session_id,
                expected,
                presented,
                matched,
                variance,
                closing_note or None,
                actor['id']
            )
        )

        audit(
            c,
            actor,
            'daily_closing.submit',
            'daily_closing',
            ident,
            {
                'location_id':location_id,
                'business_date':closing_day.isoformat(),
                'reconciliation_session_id':
                    reconciliation_session_id,
                'expected_collections':expected,
                'presented_collections':presented,
                'matched_amount':matched,
                'variance':variance,
                'closing_note':closing_note or None
            }
        )

        return {
            'id':ident,
            'merchant':merchant,
            'location_id':location_id,
            'location_name':location['name'],
            'business_date':closing_day.isoformat(),
            'reconciliation_session_id':
                reconciliation_session_id,
            'expected_collections':expected,
            'presented_collections':presented,
            'matched_amount':matched,
            'unmatched_evidence':presented-matched,
            'unmatched_ledger':expected-matched,
            'variance':variance,
            'amount_status':(
                'balanced'
                if variance==0
                else 'shortfall'
                if variance<0
                else 'overage'
            ),
            'closing_note':closing_note or None,
            'closing_status':'submitted',
            'submitted_by':actor['id']
        }


DAILY_CLOSING_APPROVE_ROLES = ('owner','admin')


def approve_daily_closing(actor,data):
    if not actor:
        raise PermissionError('Authentication required.')

    if actor['role'] not in DAILY_CLOSING_APPROVE_ROLES:
        raise PermissionError(
            'Only an owner or administrator can approve a daily closing.'
        )

    merchant=str(data.get('merchant') or '').strip()
    closing_id=str(data.get('closing_id') or '').strip()

    if not merchant:
        raise ValueError('Business is required.')

    if not closing_id:
        raise ValueError('Daily closing is required.')

    if merchant != actor['merchant']:
        raise PermissionError(
            'You cannot approve a daily closing for another business.'
        )

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')

        closing=c.execute(
            "SELECT * FROM daily_closings "
            "WHERE id=? AND merchant=?",
            (closing_id,merchant)
        ).fetchone()

        if not closing:
            raise ValueError('Daily closing was not found.')

        if closing['closing_status'] != 'submitted':
            raise ValueError(
                'Only a submitted daily closing can be approved.'
            )

        session=c.execute(
            "SELECT * FROM reconciliation_sessions "
            "WHERE id=? AND merchant=?",
            (
                closing['reconciliation_session_id'],
                merchant
            )
        ).fetchone()

        if not session:
            raise ValueError(
                'Linked reconciliation session was not found.'
            )

        if session['status'] != 'approved':
            raise ValueError(
                'Linked reconciliation session must remain approved.'
            )

        expected=int(closing['expected_collections'] or 0)
        presented=int(closing['presented_collections'] or 0)
        matched=int(closing['matched_amount'] or 0)
        variance=int(closing['variance'] or 0)

        c.execute(
            "UPDATE daily_closings "
            "SET closing_status='approved', "
            "approved_by=?, "
            "approved_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND merchant=?",
            (
                actor['id'],
                closing_id,
                merchant
            )
        )

        audit(
            c,
            actor,
            'daily_closing.approve',
            'daily_closing',
            closing_id,
            {
                'location_id':closing['location_id'],
                'business_date':closing['business_date'],
                'reconciliation_session_id':
                    closing['reconciliation_session_id'],
                'expected_collections':expected,
                'presented_collections':presented,
                'matched_amount':matched,
                'variance':variance
            }
        )

        return {
            'id':closing_id,
            'merchant':merchant,
            'location_id':closing['location_id'],
            'business_date':closing['business_date'],
            'reconciliation_session_id':
                closing['reconciliation_session_id'],
            'expected_collections':expected,
            'presented_collections':presented,
            'matched_amount':matched,
            'unmatched_evidence':presented-matched,
            'unmatched_ledger':expected-matched,
            'variance':variance,
            'amount_status':(
                'balanced'
                if variance==0
                else 'shortfall'
                if variance<0
                else 'overage'
            ),
            'closing_status':'approved',
            'approved_by':actor['id']
        }


RECONCILIATION_APPROVE_ROLES = ('owner','admin')


def approve_reconciliation_session(actor,data):
    if not actor:
        raise PermissionError('Authentication required.')

    if actor['role'] not in RECONCILIATION_APPROVE_ROLES:
        raise PermissionError(
            'Only an owner or administrator can approve reconciliation.'
        )

    merchant=str(data.get('merchant') or '').strip()
    session_id=str(data.get('session_id') or '').strip()
    exception_reason=' '.join(
        str(data.get('exception_reason') or '').strip().split()
    )

    if not merchant:
        raise ValueError('Business is required.')

    if not session_id:
        raise ValueError('Reconciliation session is required.')

    if merchant != actor['merchant']:
        raise PermissionError(
            'You cannot approve reconciliation for another business.'
        )

    if len(exception_reason) > 1000:
        raise ValueError(
            'Exception reason must not exceed 1000 characters.'
        )

    from datetime import date,datetime,time,timedelta,timezone

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')

        session=c.execute(
            "SELECT * FROM reconciliation_sessions "
            "WHERE id=? AND merchant=?",
            (session_id,merchant)
        ).fetchone()

        if not session:
            raise ValueError('Reconciliation session was not found.')

        if session['status'] != 'open':
            raise ValueError(
                'Only an open reconciliation session can be approved.'
            )

        location_id=session['location_id']

        first=date.fromisoformat(session['period_start'])
        last=date.fromisoformat(session['period_end'])

        lagos_tz=timezone(timedelta(hours=1))

        start=datetime.combine(first,time(),lagos_tz)
        end=datetime.combine(
            last+timedelta(days=1),
            time(),
            lagos_tz
        )

        bounds=[
            t.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            for t in (start,end)
        ]

        expected=int(c.execute(
            "SELECT COALESCE(SUM(paid),0) "
            "FROM entries "
            "WHERE merchant=? "
            "AND status='confirmed' "
            "AND COALESCE(location_id,?)=? "
            "AND confirmed>=? "
            "AND confirmed<?",
            (
                merchant,
                MAIN_LOCATION_ID,
                location_id,
                bounds[0],
                bounds[1]
            )
        ).fetchone()[0] or 0)

        presented=int(c.execute(
            "SELECT COALESCE(SUM(amount),0) "
            "FROM payment_records "
            "WHERE merchant=? "
            "AND location_id=? "
            "AND payment_date>=? "
            "AND payment_date<=? "
            "AND status<>'void'",
            (
                merchant,
                location_id,
                first.isoformat(),
                last.isoformat()
            )
        ).fetchone()[0] or 0)

        matched=int(c.execute(
            "SELECT COALESCE(SUM(matched_amount),0) "
            "FROM reconciliation_matches "
            "WHERE merchant=? "
            "AND session_id=? "
            "AND status='matched'",
            (merchant,session_id)
        ).fetchone()[0] or 0)

        variance=presented-expected
        unmatched_evidence=presented-matched
        unmatched_ledger=expected-matched

        perfectly_reconciled=(
            variance==0
            and unmatched_evidence==0
            and unmatched_ledger==0
        )

        if not perfectly_reconciled and not exception_reason:
            raise ValueError(
                'This reconciliation has unresolved differences. '
                'Enter an exception reason before approval.'
            )

        if perfectly_reconciled:
            exception_reason=None

        c.execute(
            "UPDATE reconciliation_sessions "
            "SET expected_collections=?, "
            "presented_collections=?, "
            "matched_amount=?, "
            "variance=?, "
            "status='approved', "
            "approved_by=?, "
            "approved=CURRENT_TIMESTAMP, "
            "exception_reason=? "
            "WHERE id=? AND merchant=?",
            (
                expected,
                presented,
                matched,
                variance,
                actor['id'],
                exception_reason,
                session_id,
                merchant
            )
        )

        audit(
            c,
            actor,
            'reconciliation.approve',
            'reconciliation_session',
            session_id,
            {
                'location_id':location_id,
                'period_start':first.isoformat(),
                'period_end':last.isoformat(),
                'expected_collections':expected,
                'presented_collections':presented,
                'matched_amount':matched,
                'unmatched_evidence':unmatched_evidence,
                'unmatched_ledger':unmatched_ledger,
                'variance':variance,
                'perfectly_reconciled':perfectly_reconciled,
                'exception_reason':exception_reason
            }
        )

        return {
            'id':session_id,
            'merchant':merchant,
            'location_id':location_id,
            'period_start':first.isoformat(),
            'period_end':last.isoformat(),
            'expected_collections':expected,
            'presented_collections':presented,
            'matched_amount':matched,
            'unmatched_evidence':unmatched_evidence,
            'unmatched_ledger':unmatched_ledger,
            'variance':variance,
            'amount_status':(
                'balanced'
                if variance==0
                else 'shortfall'
                if variance<0
                else 'overage'
            ),
            'perfectly_reconciled':perfectly_reconciled,
            'exception_reason':exception_reason,
            'approved_by':actor['id'],
            'status':'approved'
        }


RECONCILIATION_MATCH_ROLES = ('owner','admin','branch_manager')


def match_reconciliation_payment(actor,data):
    if not actor:
        raise PermissionError('Authentication required.')

    if actor['role'] not in RECONCILIATION_MATCH_ROLES:
        raise PermissionError(
            'Your role cannot match reconciliation payments.'
        )

    merchant=str(data.get('merchant') or '').strip()
    session_id=str(data.get('session_id') or '').strip()
    payment_id=str(data.get('payment_id') or '').strip()
    entry_id=str(data.get('entry_id') or '').strip()

    if not all((merchant,session_id,payment_id,entry_id)):
        raise ValueError(
            'Business, reconciliation session, payment evidence and ledger entry are required.'
        )

    if merchant != actor['merchant']:
        raise PermissionError(
            'You cannot reconcile another business.'
        )

    matched_amount=money(data.get('matched_amount',''))
    if matched_amount <= 0:
        raise ValueError('Matched amount must be positive.')

    from datetime import date,datetime,time,timedelta,timezone

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')

        session=c.execute(
            "SELECT * FROM reconciliation_sessions "
            "WHERE id=? AND merchant=?",
            (session_id,merchant)
        ).fetchone()

        if not session:
            raise ValueError('Reconciliation session was not found.')

        if session['status'] != 'open':
            raise ValueError(
                'Only an open reconciliation session can receive matches.'
            )

        location_id=session['location_id']

        if actor['role']=='branch_manager':
            if not actor_can_use_location(actor,location_id):
                raise PermissionError(
                    'This account is not assigned to the reconciliation location.'
                )

        payment=c.execute(
            "SELECT * FROM payment_records "
            "WHERE id=? AND merchant=?",
            (payment_id,merchant)
        ).fetchone()

        if not payment:
            raise ValueError('Payment evidence was not found.')

        if payment['status']=='void':
            raise ValueError('Voided payment evidence cannot be matched.')

        if payment['location_id'] != location_id:
            raise ValueError(
                'Payment evidence belongs to a different location.'
            )

        entry=c.execute(
            "SELECT id,merchant,kind,description,paid,status,"
            "COALESCE(location_id,?) location_id,confirmed "
            "FROM entries "
            "WHERE id=? AND merchant=?",
            (MAIN_LOCATION_ID,entry_id,merchant)
        ).fetchone()

        if not entry:
            raise ValueError('Ledger entry was not found.')

        if entry['status'] != 'confirmed':
            raise ValueError(
                'Only a confirmed ledger entry can be reconciled.'
            )

        if int(entry['paid'] or 0) <= 0:
            raise ValueError(
                'The selected ledger entry has no collection to reconcile.'
            )

        if entry['location_id'] != location_id:
            raise ValueError(
                'Ledger entry belongs to a different location.'
            )

        first=date.fromisoformat(session['period_start'])
        last=date.fromisoformat(session['period_end'])

        if not payment['payment_date']:
            raise ValueError(
                'Payment evidence has no payment date.'
            )

        payment_day=date.fromisoformat(payment['payment_date'])

        if payment_day < first or payment_day > last:
            raise ValueError(
                'Payment evidence falls outside the reconciliation period.'
            )

        lagos_tz=timezone(timedelta(hours=1))
        start=datetime.combine(first,time(),lagos_tz)
        end=datetime.combine(
            last+timedelta(days=1),
            time(),
            lagos_tz
        )

        bounds=[
            t.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            for t in (start,end)
        ]

        if (
            not entry['confirmed']
            or entry['confirmed'] < bounds[0]
            or entry['confirmed'] >= bounds[1]
        ):
            raise ValueError(
                'Ledger entry falls outside the reconciliation period.'
            )

        duplicate=c.execute(
            "SELECT id FROM reconciliation_matches "
            "WHERE merchant=? "
            "AND payment_id=? "
            "AND entry_id=? "
            "AND status='matched' "
            "LIMIT 1",
            (merchant,payment_id,entry_id)
        ).fetchone()

        if duplicate:
            raise ValueError(
                'This payment evidence is already matched to this ledger entry.'
            )

        payment_matched=int(c.execute(
            "SELECT COALESCE(SUM(matched_amount),0) "
            "FROM reconciliation_matches "
            "WHERE merchant=? "
            "AND payment_id=? "
            "AND status='matched'",
            (merchant,payment_id)
        ).fetchone()[0] or 0)

        entry_matched=int(c.execute(
            "SELECT COALESCE(SUM(matched_amount),0) "
            "FROM reconciliation_matches "
            "WHERE merchant=? "
            "AND entry_id=? "
            "AND status='matched'",
            (merchant,entry_id)
        ).fetchone()[0] or 0)

        payment_remaining=int(payment['amount'] or 0)-payment_matched
        entry_remaining=int(entry['paid'] or 0)-entry_matched

        if payment_remaining <= 0:
            raise ValueError(
                'This payment evidence is already fully matched.'
            )

        if entry_remaining <= 0:
            raise ValueError(
                'This ledger collection is already fully matched.'
            )

        if matched_amount > payment_remaining:
            raise ValueError(
                'Matched amount exceeds the remaining payment evidence amount.'
            )

        if matched_amount > entry_remaining:
            raise ValueError(
                'Matched amount exceeds the remaining ledger collection amount.'
            )

        import uuid
        ident=str(uuid.uuid4())

        c.execute(
            "INSERT INTO reconciliation_matches("
            "id,merchant,session_id,payment_id,entry_id,"
            "matched_amount,status,created_by"
            ") VALUES(?,?,?,?,?,?,'matched',?)",
            (
                ident,
                merchant,
                session_id,
                payment_id,
                entry_id,
                matched_amount,
                actor['id']
            )
        )

        new_payment_matched=payment_matched+matched_amount

        payment_status=(
            'matched'
            if new_payment_matched == int(payment['amount'] or 0)
            else 'partially_matched'
        )

        c.execute(
            "UPDATE payment_records SET status=? WHERE id=?",
            (payment_status,payment_id)
        )

        session_matched=int(c.execute(
            "SELECT COALESCE(SUM(matched_amount),0) "
            "FROM reconciliation_matches "
            "WHERE merchant=? "
            "AND session_id=? "
            "AND status='matched'",
            (merchant,session_id)
        ).fetchone()[0] or 0)

        c.execute(
            "UPDATE reconciliation_sessions "
            "SET matched_amount=? "
            "WHERE id=?",
            (session_matched,session_id)
        )

        audit(
            c,
            actor,
            'reconciliation.match',
            'reconciliation_match',
            ident,
            {
                'session_id':session_id,
                'payment_id':payment_id,
                'entry_id':entry_id,
                'matched_amount':matched_amount,
                'location_id':location_id
            }
        )

        return {
            'id':ident,
            'session_id':session_id,
            'payment_id':payment_id,
            'entry_id':entry_id,
            'matched_amount':matched_amount,
            'payment_amount':int(payment['amount'] or 0),
            'payment_matched':new_payment_matched,
            'payment_remaining':
                int(payment['amount'] or 0)-new_payment_matched,
            'entry_collection':int(entry['paid'] or 0),
            'entry_matched':entry_matched+matched_amount,
            'entry_remaining':
                int(entry['paid'] or 0)-(entry_matched+matched_amount),
            'session_matched_amount':session_matched,
            'session_unmatched_evidence':
                int(session['presented_collections'] or 0)-session_matched,
            'session_unmatched_ledger':
                int(session['expected_collections'] or 0)-session_matched,
            'payment_status':payment_status,
            'status':'matched'
        }


PAYMENT_METHODS = ('cash','bank_transfer','pos','other')
PAYMENT_PRESENT_ROLES = ('owner','admin','branch_manager','cashier')


def present_payment(actor,data):
    if not actor:
        raise PermissionError('Authentication required.')

    if actor['role'] not in PAYMENT_PRESENT_ROLES:
        raise PermissionError('Your role cannot submit payment evidence.')

    merchant=str(data.get('merchant') or '').strip()
    if not merchant:
        raise ValueError('Business is required.')

    if merchant != actor['merchant']:
        raise PermissionError('You cannot submit payment evidence for another business.')

    location_id=str(data.get('location_id') or '').strip()
    if not location_id:
        raise ValueError('Payment location is required.')

    method=str(data.get('method') or '').strip().lower()
    if method not in PAYMENT_METHODS:
        raise ValueError('Payment method must be cash, bank_transfer, pos or other.')

    amount=money(data.get('amount',''))
    if amount <= 0:
        raise ValueError('Payment amount must be positive.')

    institution=str(data.get('institution') or '').strip()
    reference=str(data.get('reference') or '').strip()
    payment_date=str(data.get('payment_date') or '').strip()
    entry_id=str(data.get('entry_id') or '').strip() or None

    if len(institution) > 200:
        raise ValueError('Institution must be 200 characters or fewer.')

    if len(reference) > 200:
        raise ValueError('Payment reference must be 200 characters or fewer.')

    if method in ('bank_transfer','pos') and not reference:
        raise ValueError('Bank transfer and POS payments require a payment reference.')

    if not payment_date:
        raise ValueError('Payment date is required.')

    from datetime import date
    try:
        date.fromisoformat(payment_date)
    except ValueError:
        raise ValueError('Payment date must use YYYY-MM-DD format.')

    with connect() as c:
        c.execute('BEGIN IMMEDIATE')

        require_location(c,merchant,location_id)

        if actor['role'] in ('branch_manager','cashier'):
            if not actor_can_use_location(actor,location_id):
                raise PermissionError(
                    'This account is not assigned to the selected payment location.'
                )

        entry=None
        if entry_id:
            entry=c.execute(
                "SELECT id,merchant,kind,paid,status,"
                "COALESCE(location_id,?) location_id "
                "FROM entries WHERE id=?",
                (MAIN_LOCATION_ID,entry_id)
            ).fetchone()

            if not entry or entry['merchant'] != merchant:
                raise ValueError('Selected ledger entry was not found for this business.')

            if entry['status'] != 'confirmed':
                raise ValueError('Payment evidence can only be linked to a confirmed entry.')

            if entry['location_id'] != location_id:
                raise ValueError(
                    'Payment evidence location must match the linked ledger entry location.'
                )

            if int(entry['paid'] or 0) <= 0:
                raise ValueError('The linked ledger entry has no recorded collection to reconcile.')

        if reference:
            duplicate=c.execute(
                "SELECT id FROM payment_records "
                "WHERE merchant=? AND lower(reference)=lower(?)",
                (merchant,reference)
            ).fetchone()

            if duplicate:
                raise ValueError(
                    'This payment reference has already been presented for reconciliation.'
                )

        import uuid
        ident=str(uuid.uuid4())

        c.execute(
            "INSERT INTO payment_records("
            "id,merchant,entry_id,location_id,method,amount,"
            "institution,reference,payment_date,status,submitted_by"
            ") VALUES(?,?,?,?,?,?,?,?,?,'presented',?)",
            (
                ident,
                merchant,
                entry_id,
                location_id,
                method,
                amount,
                institution or None,
                reference or None,
                payment_date,
                actor['id']
            )
        )

        audit(
            c,
            actor,
            'payment.present',
            'payment_record',
            ident,
            {
                'entry_id':entry_id,
                'location_id':location_id,
                'method':method,
                'amount':amount,
                'institution':institution or None,
                'reference':reference or None,
                'payment_date':payment_date
            }
        )

        return {
            'id':ident,
            'merchant':merchant,
            'entry_id':entry_id,
            'location_id':location_id,
            'method':method,
            'amount':amount,
            'institution':institution or None,
            'reference':reference or None,
            'payment_date':payment_date,
            'status':'presented',
            'submitted_by':actor['id']
        }


class Handler(BaseHTTPRequestHandler):

    def respond(self, code, obj):

        raw = json.dumps(obj).encode()

        self.send_response(code)

        self.send_header('Content-Type', 'application/json')

        self.send_header('Content-Length', str(len(raw)))

        self.send_header('Cache-Control', 'no-store')

        self.end_headers()

        self.wfile.write(raw)



    def do_GET(self):

        parsed = urlparse(self.path)



        if parsed.path == '/webhook/whatsapp':

            params = parse_qs(parsed.query)

            mode = params.get('hub.mode', [''])[0]

            token = params.get('hub.verify_token', [''])[0]

            challenge = params.get('hub.challenge', [''])[0]



            if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:

                raw = challenge.encode('utf-8')

                self.send_response(200)

                self.send_header('Content-Type', 'text/plain; charset=utf-8')

                self.send_header('Content-Length', str(len(raw)))

                self.send_header('Cache-Control', 'no-store')

                self.end_headers()

                self.wfile.write(raw)

                return



            return self.respond(403, {'error': 'Webhook verification failed'})



        if parsed.path == '/':

            raw = (

                Path(__file__)

                .with_name('dashboard.html')

                .read_text(encoding='utf-8')

                .encode()

            )

            self.send_response(200)

            self.send_header('Content-Type', 'text/html; charset=utf-8')

            self.send_header('Content-Length', str(len(raw)))

            self.end_headers()

            self.wfile.write(raw)



        elif parsed.path == '/api/auth/status':
            self.respond(200, auth_status())
        elif parsed.path == '/api/auth/me':
            actor=authenticate_token(self.headers.get('X-Session-Token'))
            if not actor:return self.respond(401, {'error':'Authentication required'})
            self.respond(200, {'user':actor})
        elif parsed.path == '/api/backup':
            actor=authenticate_token(self.headers.get('X-Session-Token'))
            if not actor:return self.respond(401, {'error':'Authentication required'})
            if actor['role'] not in AUTH_MANAGE_ROLES:return self.respond(403, {'error':'Only an owner or administrator can create backups.'})
            from datetime import datetime, timezone
            raw=backup_database();stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
            self.send_response(200);self.send_header('Content-Type','application/octet-stream')
            self.send_header('Content-Disposition','attachment; filename="voice-ledger-'+stamp+'.sqlite3"')
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
        elif parsed.path in (
            '/api/staging/inventory/products',
            '/api/staging/inventory/balance',
            '/api/staging/inventory/review',
        ):
            actor=authenticate_token(
                self.headers.get('X-Session-Token')
            )
            if not actor:
                return self.respond(
                    401, {'error':'Authentication required'}
                )
            if actor.get('must_change_password'):
                return self.respond(
                    403, {'error':'Password change required'}
                )
            try:
                from omata_pg_http_inventory import (
                    execute_inventory_action
                )
                from urllib.parse import parse_qs

                if parsed.path.endswith('/products'):
                    action='products'
                    payload={}
                elif parsed.path.endswith('/balance'):
                    action='balance'
                    params=parse_qs(parsed.query)
                    payload={
                        'product':params.get('product',[''])[0],
                        'location_id':params.get('location_id',[''])[0],
                    }
                else:
                    action='review'
                    params=parse_qs(parsed.query)
                    payload={
                        'id':params.get('id',[''])[0],
                    }

                result=execute_inventory_action(
                    action,payload,actor
                )
                return self.respond(200,result)
            except PermissionError:
                return self.respond(
                    403, {'error':'Inventory access forbidden'}
                )
            except (ValueError,TypeError):
                return self.respond(
                    400, {'error':'Invalid inventory request'}
                )
            except Exception:
                return self.respond(
                    503, {'error':'Inventory service unavailable'}
                )

        # OMATA_STEP_172M_CUSTOMER_RESOLUTION_ROUTE
        elif parsed.path == '/api/staging/customers/resolve':
            actor=authenticate_token(self.headers.get('X-Session-Token'))
            if not actor:
                return self.respond(401, {'error':'Authentication required'})
            if actor.get('must_change_password'):
                return self.respond(403, {'error':'Password change required'})
            try:
                from urllib.parse import parse_qs
                params=parse_qs(parsed.query, keep_blank_values=True)
                if set(params) != {'query'} or len(params['query']) != 1:
                    raise ValueError('Exactly one customer query required')
                query=params['query'][0]
                if not query.strip() or len(query) > 200:
                    raise ValueError('Invalid customer query')
                from omata_pg_http_customers import execute_customer_action
                result=execute_customer_action(
                    'resolve', {'query':query}, actor
                )
                return self.respond(200,result)
            except PermissionError:
                return self.respond(403, {'error':'Customer access forbidden'})
            except (ValueError,TypeError):
                return self.respond(400, {'error':'Invalid customer query'})
            except Exception:
                return self.respond(503, {'error':'Customer service unavailable'})

        elif parsed.path == '/api/staging/customers':
            actor=authenticate_token(self.headers.get('X-Session-Token'))
            if not actor:
                return self.respond(401, {'error':'Authentication required'})
            if actor.get('must_change_password'):
                return self.respond(403, {'error':'Password change required'})
            try:
                from omata_pg_http_customers import execute_customer_action
                result=execute_customer_action('list', {}, actor)
                return self.respond(200,result)
            except PermissionError:
                return self.respond(403, {'error':'Customer access forbidden'})
            except Exception:
                return self.respond(503, {'error':'Customer service unavailable'})

        elif parsed.path == '/api/staging/finance-preview':
            actor=authenticate_token(self.headers.get('X-Session-Token'))
            if not actor:return self.respond(401, {'error':'Authentication required'})
            if actor.get('must_change_password'):
                return self.respond(403, {'error':'Password change required before using the ledger.'})
            try:
                from omata_pg_preview import preview_finance
                self.respond(200, preview_finance(actor))
            except PermissionError:
                self.respond(403, {'error':'Staging finance preview unavailable for this account'})
            except Exception:
                # Never echo DSNs, SQL exceptions or customer contents to clients.
                self.respond(503, {'error':'Staging finance preview unavailable'})

        elif parsed.path == '/api/state':
            actor=authenticate_token(self.headers.get('X-Session-Token'))
            if not actor:return self.respond(401, {'error':'Authentication required'})
            if actor.get('must_change_password'):
                return self.respond(403, {'error':'Password change required before using the ledger.'})
            self.respond(200, filter_state_for_user(state(),actor))



        else:

            self.respond(404, {'error': 'Not found'})



    def do_POST(self):

        parsed = urlparse(self.path)



        # OMATA_STEP_187G_STAGING_APPROVAL_ROUTE
        if parsed.path == '/api/staging/whatsapp/approval':
            import os

            # Disabled unless explicitly enabled in a controlled
            # local staging test process.
            if os.environ.get('OMATA_PG_WHATSAPP_HTTP_TEST') != 'enabled':
                return self.respond(404, {'error': 'Not found'})

            # Independent localhost restriction.
            if self.client_address[0] not in ('127.0.0.1', '::1'):
                return self.respond(403, {'error': 'Localhost only'})

            # Never accept secrets, route permissions, entry IDs or
            # authentication flags from request JSON.
            secret = os.environ.get('META_APP_SECRET', '')
            if not secret:
                return self.respond(503, {'error': 'Webhook secret unavailable'})

            # Intentionally fail closed until trusted sender-to-merchant
            # authorization is configured server-side.
            from omata_whatsapp_pg_staging_routes import (
                get_staging_approval_routes,
            )
            trusted_routes = get_staging_approval_routes()

            if not trusted_routes:
                return self.respond(503, {
                    'error': 'Staging merchant authorization not configured'
                })

            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > 200000:
                    return self.respond(400, {'error': 'Invalid request size'})

                raw_body = self.rfile.read(size)

                from omata_whatsapp_pg_http_route import (
                    process_staging_http_approval,
                )

                result = process_staging_http_approval(
                    raw_body=raw_body,
                    signature_header=self.headers.get(
                        'X-Hub-Signature-256', ''
                    ),
                    app_secret=secret,
                    trusted_routes=trusted_routes,
                    # OMATA_STEP_187P_DURABLE_APPROVAL
                    # Persistence is controlled only by server environment.
                    commit=all(
                        os.environ.get(flag) == 'enabled'
                        for flag in (
                            'OMATA_PG_WHATSAPP_HTTP_TEST',
                            'OMATA_PG_SYNTHETIC_ROUTES',
                            'OMATA_PG_APPROVAL_PERSIST_TEST',
                        )
                    ),
                )

                return self.respond(200, result)

            except PermissionError:
                return self.respond(403, {'error': 'Request forbidden'})
            except (ValueError, TypeError):
                return self.respond(400, {'error': 'Invalid request'})
            except Exception:
                return self.respond(503, {
                    'error': 'Staging approval processing unavailable'
                })

        if parsed.path == '/webhook/whatsapp':
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > 200000:
                    raise ValueError('Invalid webhook request size.')

                raw_body = self.rfile.read(size)
                if not verify_whatsapp_signature(raw_body, self.headers.get('X-Hub-Signature-256')):
                    return self.respond(403, {'error': 'Invalid webhook signature'})

                data = json.loads(raw_body)
                messages = extract_whatsapp_text_messages(data)
                audio_messages = extract_whatsapp_audio_messages(data)

                saved = []
                for message in messages:
                    inbox_id = inbox_add(
                        WHATSAPP_DEFAULT_MERCHANT,
                        message['provider_id'],
                        message['sender'],
                        message['receiver'],
                        message['body'],
                        'whatsapp'
                    )
                    saved.append(inbox_id)

                voice_job_ids = []
                for message in audio_messages:
                    job_id, created = voice_job_add(
                        WHATSAPP_DEFAULT_MERCHANT,
                        message['provider_id'],
                        message['sender'],
                        message['receiver'],
                        message['media_id'],
                        message['mime_type']
                    )
                    voice_job_ids.append(job_id)
                    if created:
                        start_voice_job(job_id)

                print(
                    'WHATSAPP WEBHOOK:',
                    json.dumps({
                        'received_text_messages': len(messages),
                        'saved_inbox_ids': saved,
                        'received_audio_messages': len(audio_messages),
                        'voice_job_ids': voice_job_ids,
                    }, ensure_ascii=False),
                    flush=True
                )

                # Meta expects a fast acknowledgement. Audio transcription continues
                # in a background thread after the webhook response is returned.
                return self.respond(200, {
                    'ok': True,
                    'received_text_messages': len(messages),
                    'saved': len(saved),
                    'received_audio_messages': len(audio_messages),
                    'voice_jobs': len(voice_job_ids),
                })

            except (ValueError, TypeError, json.JSONDecodeError) as e:
                return self.respond(400, {'error': str(e)})

        if parsed.path in ('/api/auth/bootstrap','/api/auth/login'):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>20000:raise ValueError('Invalid request size.')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('Expected an object.')
                result=bootstrap_owner(data) if parsed.path=='/api/auth/bootstrap' else login_user(data)
                return self.respond(200,result)
            except (ValueError,TypeError,json.JSONDecodeError) as e:
                return self.respond(400,{'error':str(e)})
        # OMATA_STEP_179E_EARLY_ATOMIC_GATE
        # Hide disabled atomic-sale routes before global authentication.
        if parsed.path in (
            '/api/staging/atomic-sale/drafts',
            '/api/staging/atomic-sale/review',
            '/api/staging/atomic-sale/confirm',
            '/api/staging/atomic-sale/status',
        ):
            if __import__('os').environ.get('OMATA_PG_ATOMIC_HTTP_TEST') != 'enabled':
                return self.respond(404, {'error': 'Not found'})

        actor=authenticate_token(self.headers.get('X-Session-Token'))
        if not actor:return self.respond(401, {'error':'Authentication required'})
        if parsed.path in (
            '/api/staging/inventory/products',
            '/api/staging/inventory/drafts',
            '/api/staging/inventory/confirm',
            '/api/staging/inventory/confirm-dry-run',
        ):
            if actor.get('must_change_password'):
                return self.respond(
                    403, {'error':'Password change required'}
                )

            # OMATA_STEP_170C_DRY_RUN_ROUTE
            if parsed.path == '/api/staging/inventory/confirm-dry-run':
                import os
                if os.environ.get('OMATA_PG_DRY_RUN_HTTP_TEST') != 'enabled':
                    # Consume only bounded, well-formed request bodies before
                    # rejecting. Never dispatch inventory confirmation here.
                    self.close_connection = True
                    try:
                        size = int(self.headers.get('Content-Length', '0'))
                    except (ValueError, TypeError):
                        size = -1

                    if 0 <= size <= 20000:
                        if size:
                            self.rfile.read(size)

                    return self.respond(
                        403, {'error':'Inventory dry-run testing disabled'}
                    )

            try:
                size=int(
                    self.headers.get('Content-Length','0')
                )
                if size<=0 or size>20000:
                    raise ValueError('Invalid request size')

                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):
                    raise ValueError('Expected request object')

                require_actor_merchant(actor,data)

                from omata_pg_http_inventory import (
                    execute_inventory_action
                )

                actions={
                    '/api/staging/inventory/products':'register',
                    '/api/staging/inventory/drafts':'draft',
                    '/api/staging/inventory/confirm':'confirm',
                    '/api/staging/inventory/confirm-dry-run':'confirm_dry_run',
                }

                action=actions[parsed.path]
                result=execute_inventory_action(
                    action,data,actor
                )
                return self.respond(200,result)

            except PermissionError:
                return self.respond(
                    403, {'error':'Inventory operation forbidden'}
                )
            except (ValueError,TypeError,json.JSONDecodeError):
                return self.respond(
                    400, {'error':'Invalid inventory request'}
                )
            except Exception:
                return self.respond(
                    503, {'error':'Inventory service unavailable'}
                )

        if parsed.path == '/api/staging/customers':
            if actor.get('must_change_password'):
                return self.respond(403, {'error':'Password change required'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>20000:
                    raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):
                    raise ValueError('Expected request object')
                require_actor_merchant(actor,data)
                from omata_pg_http_customers import execute_customer_action
                return self.respond(
                    200,execute_customer_action('create',data,actor)
                )
            except PermissionError:
                return self.respond(403, {'error':'Customer registration forbidden'})
            except (ValueError,TypeError,json.JSONDecodeError):
                return self.respond(400, {'error':'Invalid customer request'})
            except Exception:
                return self.respond(503, {'error':'Customer service unavailable'})
        # OMATA_STEP_178Q_ATOMIC_HTTP_ROUTES
        if parsed.path in (
            '/api/staging/atomic-sale/drafts',
            '/api/staging/atomic-sale/review',
            '/api/staging/atomic-sale/confirm',
            '/api/staging/atomic-sale/status',
        ):
            # Entire HTTP feature remains disabled unless separately enabled.
            if __import__('os').environ.get('OMATA_PG_ATOMIC_HTTP_TEST') != 'enabled':
                return self.respond(404, {'error': 'Not found'})

            actor = authenticate_token(
                self.headers.get('X-Session-Token')
            )
            if not actor:
                return self.respond(
                    401, {'error': 'Authentication required'}
                )

            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > 20000:
                    raise ValueError('Invalid request size')

                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Expected request object')

                require_actor_merchant(actor, data)

                from omata_pg_atomic_sale_service import execute_atomic_sale

                actions = {
                    '/api/staging/atomic-sale/drafts': 'draft',
                    '/api/staging/atomic-sale/review': 'review',
                    '/api/staging/atomic-sale/confirm': 'confirm',
                    '/api/staging/atomic-sale/status': 'status',
                }

                action = actions[parsed.path]
                result = execute_atomic_sale(action, data, actor)

                # Never represent an unresolved commit as success.
                if isinstance(result, dict) and result.get('commit_uncertain'):
                    return self.respond(
                        409,
                        {
                            'error': 'Transaction outcome requires reconciliation',
                            'retry_allowed': False,
                            'reconciliation': result.get('reconciliation'),
                        }
                    )

                return self.respond(200, result)

            except PermissionError:
                return self.respond(
                    403, {'error': 'Atomic-sale operation forbidden'}
                )
            except (ValueError, TypeError, json.JSONDecodeError):
                return self.respond(
                    400, {'error': 'Invalid atomic-sale request'}
                )
            except Exception:
                # Do not expose database errors, signing secrets or DSNs.
                return self.respond(
                    503, {'error': 'Atomic-sale service unavailable'}
                )

        if parsed.path in ('/api/staging/finance-drafts', '/api/staging/finance-confirm'):
            if actor.get('must_change_password'):
                return self.respond(403, {'error':'Password change required before using the ledger.'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>20000:raise ValueError('Invalid request size.')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('Expected an object.')
                require_actor_merchant(actor,data)
                from omata_pg_http_finance import execute_staging_finance
                action='draft' if parsed.path.endswith('finance-drafts') else 'confirm'
                return self.respond(200,execute_staging_finance(action,data,actor))
            except PermissionError:
                return self.respond(403, {'error':'Staging financial operation forbidden'})
            except (ValueError,TypeError,json.JSONDecodeError):
                return self.respond(400, {'error':'Invalid staging financial request'})
            except Exception:
                return self.respond(503, {'error':'Staging financial operation unavailable'})
        if parsed.path=='/api/auth/logout':
            logout_user(self.headers.get('X-Session-Token'),actor);return self.respond(200,{'ok':True})

        if parsed.path=='/api/auth/change-password':
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>20000:raise ValueError('Invalid request size.')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('Expected an object.')
                return self.respond(200,change_own_password(actor,self.headers.get('X-Session-Token'),data))
            except (ValueError,TypeError,json.JSONDecodeError) as e:
                return self.respond(400,{'error':str(e)})

        if actor.get('must_change_password'):
            return self.respond(403, {'error':'Password change required before using the ledger.'})

        try:

            size = int(self.headers.get('Content-Length', '0'))



            if parsed.path == '/api/transcribe':

                if size <= 0 or size > voice.MAX_AUDIO_BYTES:

                    raise ValueError('Audio must be under 10 MB.')

                self.respond(200, voice.transcribe(self.rfile.read(size)))

                return



            if size <= 0 or size > 20000:

                raise ValueError('Invalid request size.')



            data = json.loads(self.rfile.read(size))

            if not isinstance(data, dict):

                raise ValueError('Expected an object.')
            require_actor_merchant(actor,data)
            read_like={'/api/parse','/api/inbox/parse','/api/voice/process','/api/daily','/api/report-period'}
            location_sale_routes={'/api/drafts','/api/confirm','/api/reverse'}
            branch_stock_routes={'/api/stock/drafts','/api/stock/confirm','/api/stock/reverse',
                                 '/api/transfers/drafts','/api/transfers/confirm','/api/transfers/reverse',
                                 '/api/pricing/branch'}
            payment_routes={
                '/api/payments/present',
                '/api/reconciliation/create',
                '/api/reconciliation/match',
                '/api/reconciliation/approve',
                '/api/daily-closing/submit',
                '/api/daily-closing/approve'
            }
            user_routes={'/api/users','/api/users/update','/api/users/status','/api/users/reset-password'}
            if parsed.path in user_routes:
                if actor['role'] not in AUTH_MANAGE_ROLES:
                    raise PermissionError('Only an owner or administrator can manage users.')
            elif (
                parsed.path not in read_like
                and parsed.path not in location_sale_routes
                and parsed.path not in branch_stock_routes
                and parsed.path not in payment_routes
            ):
                require_mutation_role(actor)

            if parsed.path == '/api/users':
                result={'user':create_user(actor,data)}
            elif parsed.path == '/api/users/update':
                result={'user':update_user(actor,data)}
            elif parsed.path == '/api/users/status':
                result={'user':set_user_active(actor,data)}
            elif parsed.path == '/api/users/reset-password':
                result=reset_user_password(actor,data)
            elif parsed.path == '/api/payments/present':
                result={'payment':present_payment(actor,data)}

            elif parsed.path == '/api/reconciliation/create':
                result={
                    'session':create_reconciliation_session(actor,data)
                }

            elif parsed.path == '/api/reconciliation/match':
                result={
                    'match':match_reconciliation_payment(actor,data)
                }

            elif parsed.path == '/api/reconciliation/approve':
                result={
                    'session':approve_reconciliation_session(actor,data)
                }

            elif parsed.path == '/api/daily-closing/submit':
                result={
                    'closing':submit_daily_closing(actor,data)
                }

            elif parsed.path == '/api/daily-closing/approve':
                result={
                    'closing':approve_daily_closing(actor,data)
                }

            elif parsed.path == '/api/parse':

                result = parse_for_review(data.get('merchant'), data.get('text'), data.get('kind'))



            elif parsed.path == '/api/inbox/parse':

                result = inbox_parse(data.get('id'), data.get('kind'))

            elif parsed.path == '/api/voice/process':

                result = {'inbox_id': process_voice_job(data.get('id'))}



            elif parsed.path == '/api/inbox/simulate':

                result = {

                    'id': inbox_add(

                        data.get('merchant'),

                        'sim:' + str(uuid.uuid4()),

                        'local-test',

                        'local-test',

                        data.get('text'),

                        'simulation'

                    )

                }



            elif parsed.path == '/api/shops':

                add_shop(data)

                result = {'ok': True}



            elif parsed.path == '/api/daily':

                result = daily_report(data.get('merchant'), data.get('date'), actor)

            elif parsed.path == '/api/report-period':

                result = period_report(
                    data.get('merchant'),
                    data.get('start_date'),
                    data.get('end_date'),
                    actor,
                    bool(data.get('compare',True)),
                    data.get('previous_start_date'),
                    data.get('previous_end_date')
                )



            elif parsed.path == '/api/pricing/base':
                result=set_product_base_price(actor,data)
            elif parsed.path == '/api/pricing/branch':
                result=set_branch_price(actor,data)
            elif parsed.path == '/api/products':

                product(data)

                result = {'ok': True}

            elif parsed.path == '/api/suppliers':
                supplier(data)
                result = {'ok': True}

            elif parsed.path == '/api/locations':
                location(data)
                result = {'ok': True}

            elif parsed.path == '/api/transfers/drafts':
                result = {'id': transfer_draft(data, actor)}

            elif parsed.path == '/api/transfers/confirm':
                transfer_action(data.get('id'), 'confirm', actor=actor)
                result = {'ok': True}

            elif parsed.path == '/api/transfers/reverse':
                transfer_action(data.get('id'), 'reverse', data.get('reason'), actor)
                result = {'ok': True}



            elif parsed.path == '/api/stock/drafts':

                result = {'id': stock_draft(data, actor)}



            elif parsed.path == '/api/stock/confirm':

                stock_action(data.get('id'), 'confirm', actor=actor)

                result = {'ok': True}



            elif parsed.path == '/api/stock/cost':

                stock_set_cost(data.get('id'), data.get('total_cost'))

                result = {'ok': True}



            elif parsed.path == '/api/stock/reverse':

                stock_action(data.get('id'), 'reverse', data.get('reason'), actor)

                result = {'ok': True}



            elif parsed.path == '/api/drafts':

                result = {'id': draft(data, actor)}



            elif parsed.path == '/api/customers':

                customer(data)

                result = {'ok': True}

            elif parsed.path == '/api/customers/from-suggestion':

                result = create_customer_from_suggestion(data.get('merchant'), data.get('name'))



            elif parsed.path == '/api/reverse':

                reverse(data.get('id'), data.get('reason'), actor)

                result = {'ok': True}



            elif parsed.path == '/api/confirm':

                confirm(data.get('id'), actor)

                result = {'ok': True}



            else:

                return self.respond(404, {'error': 'Not found'})



            self.respond(200, result)



        except PermissionError as e:
            self.respond(403, {'error': str(e)})
        except (ValueError, TypeError, json.JSONDecodeError) as e:

            self.respond(400, {'error': str(e)})



    def log_message(self, *args):

        pass





if __name__=='__main__':

    connect().close()
    resume_pending_voice_jobs()

    print('Local pilot dashboard: http://127.0.0.1:8080',flush=True)
    print('WhatsApp Graph version:', WHATSAPP_GRAPH_VERSION, flush=True)
    print('WhatsApp access token loaded:', bool(WHATSAPP_ACCESS_TOKEN), flush=True)
    print('WhatsApp signature verification:', 'enabled' if WHATSAPP_APP_SECRET else 'not configured', flush=True)

    HTTPServer(('127.0.0.1',8080),Handler).serve_forever()

