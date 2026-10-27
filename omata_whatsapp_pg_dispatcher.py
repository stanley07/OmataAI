"""Synthetic-only PostgreSQL outbox dispatcher.

No Meta API integration. No automatic commits.
Caller controls transaction rollback or commit.
"""

from omata_whatsapp_pg_outbox_guard import (
    STAGING_MERCHANT,
    validate_staging_outbox_record,
)


def simulate_claim(
    db,
    *,
    merchant,
    provider_key,
):
    if merchant != STAGING_MERCHANT:
        raise PermissionError("Staging merchant required")

    # Lock and inspect only the explicitly identified message.
    with db.cursor() as cur:
        cur.execute("""
            SELECT provider_key, sender, receiver,
                   reply_text, state
            FROM public.whatsapp_reply_outbox
            WHERE provider_key = %s
            FOR UPDATE NOWAIT
        """, (provider_key,))

        row = cur.fetchone()

    if row is None:
        raise ValueError("Outbox message not found")

    if isinstance(row, dict):
        record = row
    else:
        record = dict(zip(
            ("provider_key", "sender", "receiver",
             "reply_text", "state"),
            row,
        ))

    validate_staging_outbox_record(
        merchant=merchant,
        provider_key=record["provider_key"],
        sender=record["sender"],
        receiver=record["receiver"],
        reply_text=record["reply_text"],
    )

    if record["state"] != "pending":
        raise ValueError("Only pending messages may be claimed")

    with db.cursor() as cur:
        cur.execute("""
            UPDATE public.whatsapp_reply_outbox
            SET state = 'sending',
                updated_at = CURRENT_TIMESTAMP
            WHERE provider_key = %s
              AND state = 'pending'
            RETURNING provider_key
        """, (provider_key,))

        claimed = cur.fetchone()

    if claimed is None:
        raise RuntimeError("Concurrent claim conflict")

    return {
        "provider_key": provider_key,
        "simulation": True,
        "meta_called": False,
        "state_in_transaction": "sending",
    }
