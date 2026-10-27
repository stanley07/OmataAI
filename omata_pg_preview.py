"""Read-only PostgreSQL staging preview; not a replacement for the SQLite dashboard."""
import os
from urllib.parse import urlsplit
from datetime import date, datetime
from decimal import Decimal
from omata_pg_finance_read import finance_snapshot

TEST_HOST = 'ep-dawn-night-b9t7gdwg.c-7.eu-central-1.aws.neon.tech'


def preview_finance(actor):
    if os.environ.get('OMATA_PG_FINANCE_PREVIEW') != 'enabled':
        raise PermissionError('Staging finance preview is disabled')
    if not isinstance(actor, dict) or not actor.get('id') or not actor.get('merchant'):
        raise PermissionError('Authenticated actor required')
    url = os.environ.get('OMATA_PG_STAGING_URL', '').strip()
    p = urlsplit(url)
    if p.scheme not in ('postgres', 'postgresql') or p.hostname != TEST_HOST or p.path != '/neondb' or not p.username or not p.password:
        raise RuntimeError('Staging endpoint verification failed')
    import psycopg
    from psycopg.rows import dict_row
    db = psycopg.connect(url, sslmode='require', connect_timeout=15, row_factory=dict_row, autocommit=False)
    try:
        with db.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
        data = finance_snapshot(db, actor=actor)
        def compatible(v):
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            if isinstance(v, Decimal):
                return int(v) if v == v.to_integral_value() else str(v)
            if isinstance(v, dict):
                return {k: compatible(x) for k, x in v.items()}
            if isinstance(v, list):
                return [compatible(x) for x in v]
            return v
        return compatible(data)
    finally:
        db.rollback()
        db.close()
