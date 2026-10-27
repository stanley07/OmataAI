"""Separate, opt-in TEST-BRANCH-ONLY financial write HTTP helpers.
Not attached to normal SQLite /api/drafts or /api/confirm routes.
"""
import os
from urllib.parse import urlsplit
from omata_pg_finance_adapter import draft_with_existing_connection, confirm_with_existing_connection

TEST_HOST = 'ep-dawn-night-b9t7gdwg.c-7.eu-central-1.aws.neon.tech'

def _guard(actor):
    if os.environ.get('OMATA_PG_FINANCE_WRITE_TEST') != 'enabled':
        raise PermissionError('Staging financial writes disabled')
    if os.environ.get('OMATA_PG_FINANCE_PREVIEW') != 'enabled':
        raise PermissionError('Staging financial view must be enabled alongside writes')
    if os.environ.get('OMATA_PG_ALLOW_STAGING_WRITES') != 'I_UNDERSTAND':
        raise PermissionError('Staging write authorization missing')
    if not isinstance(actor, dict) or not actor.get('id') or not actor.get('merchant') or actor.get('must_change_password'):
        raise PermissionError('Authenticated actor required')
    url = os.environ.get('OMATA_PG_STAGING_URL', '').strip()
    p = urlsplit(url)
    if (p.scheme not in ('postgres', 'postgresql') or p.hostname != TEST_HOST
            or p.path != '/neondb' or not p.username or not p.password):
        raise RuntimeError('Testing branch endpoint validation failed')
    return url

def execute_staging_finance(action, payload, actor):
    url = _guard(actor)
    if action not in ('draft', 'confirm') or not isinstance(payload, dict):
        raise ValueError('Unsupported financial test action')
    if action == 'confirm' and payload.get('human_confirmed') is not True:
        raise PermissionError('Explicit human confirmation required')
    # Open connection only after all preconditions validated; no SQLite fallback.
    import psycopg
    from psycopg.rows import dict_row
    db = psycopg.connect(url, sslmode='require', connect_timeout=15, row_factory=dict_row, autocommit=False)
    try:
        if action == 'draft':
            entry_id = draft_with_existing_connection(db, payload, actor)
            result = {'id': entry_id, 'status': 'draft', 'database': 'neon-finance-testing'}
        else:
            entry_id = str(payload.get('id') or '').strip()
            confirmed = confirm_with_existing_connection(db, entry_id, actor)
            result = {'ok': True, 'confirmed_now': bool(confirmed), 'database': 'neon-finance-testing'}
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
