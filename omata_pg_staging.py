"""Isolated PostgreSQL staging primitives. NOT connected to the live application.

Does not load WhatsApp credentials, send messages, or create schema.
Set OMATA_PG_STAGING_URL only in a private shell session; never commit it.
"""
import os
from contextlib import contextmanager
from psycopg import connect, sql
from psycopg.rows import dict_row

class StagingGuardError(RuntimeError):
    pass

@contextmanager
def staging_connection(*, writable=False):
    url = os.environ.get('OMATA_PG_STAGING_URL', '').strip()
    if not url:
        raise StagingGuardError('OMATA_PG_STAGING_URL is required')
    if writable and os.environ.get('OMATA_PG_ALLOW_STAGING_WRITES') != 'I_UNDERSTAND':
        raise StagingGuardError('Staging write opt-in missing')
    with connect(url, sslmode='require', connect_timeout=15, row_factory=dict_row) as db:
        with db.cursor() as cur:
            if not writable:
                cur.execute('SET TRANSACTION READ ONLY')
            cur.execute('SELECT current_database() AS db_name, current_setting(\'transaction_read_only\') AS read_only')
            result = cur.fetchone()
            if result['db_name'] != 'neondb':
                raise StagingGuardError('Unexpected database')
            if writable and result['read_only'] != 'off':
                raise StagingGuardError('Database is read-only')
        yield db
        if not writable:
            db.rollback()


def inventory_counts():
    """Read-only list of staging table counts, without exposing any customer data."""
    with staging_connection() as db:
        with db.cursor() as cur:
            cur.execute("""SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename""")
            names = [r['tablename'] for r in cur.fetchall()]
            return {name: _count(cur, name) for name in names}


def _count(cursor, name):
    cursor.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(name)))
    return cursor.fetchone()['n']


def stage_enqueue_reply(db, *, provider_key, sender, receiver, reply_text):
    """Idempotently queue a reply in STAGING. Never sends it to Meta."""
    if not all(isinstance(x, str) and x.strip() for x in
               (provider_key, sender, receiver, reply_text)):
        raise ValueError('Missing required reply field')
    if len(reply_text) > 4096:
        raise ValueError('Reply exceeds limit')
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO whatsapp_reply_outbox
                (provider_key, sender, receiver, reply_text)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (provider_key) DO NOTHING
            RETURNING provider_key
        """, (provider_key, sender, receiver, reply_text))
        return cur.fetchone() is not None


def stage_claim_reply(db):
    """Concurrency-safe claim for a staging test; does not transmit messages.

    Caller must commit the transaction. Existing 'sending' or 'review' rows are
    never reclaimed automatically because external delivery might be uncertain.
    """
    with db.cursor() as cur:
        cur.execute("""
            WITH picked AS (
                SELECT provider_key
                FROM whatsapp_reply_outbox
                WHERE state = 'pending'
                ORDER BY created_at, provider_key
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE whatsapp_reply_outbox AS o
            SET state = 'sending', updated_at = CURRENT_TIMESTAMP
            FROM picked
            WHERE o.provider_key = picked.provider_key
            RETURNING o.provider_key, o.sender, o.receiver, o.reply_text
        """)
        return cur.fetchone()
