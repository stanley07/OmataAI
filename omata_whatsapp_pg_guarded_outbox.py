"""Guarded PostgreSQL outbox enqueue for synthetic staging tests.

No direct Meta calls. Caller owns transaction and commit/rollback.
"""

import hashlib

from omata_pg_staging import stage_enqueue_reply
from omata_whatsapp_pg_outbox_guard import (
    validate_staging_outbox_record,
)


def guarded_enqueue_review(
    db,
    *,
    merchant,
    entry_id,
    delivery,
):
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ValueError("Missing entry ID")

    if not isinstance(delivery, dict):
        raise ValueError("Invalid delivery object")

    if delivery.get("dry_run") is not True:
        raise PermissionError("Only dry-run staging delivery allowed")

    if delivery.get("sent") is not False:
        raise PermissionError("Already-sent delivery refused")

    payload = delivery.get("payload") or {}

    if payload.get("type") != "text":
        raise ValueError("Only text review supported")

    body = (payload.get("text") or {}).get("body")

    provider_key = "omata-review-v1:" + hashlib.sha256(
        f"{merchant}:{entry_id}".encode("utf-8")
    ).hexdigest()

    sender = payload.get("to")
    receiver = delivery.get("phone_number_id")

    validate_staging_outbox_record(
        merchant=merchant,
        provider_key=provider_key,
        sender=sender,
        receiver=receiver,
        reply_text=body,
    )

    with db.cursor() as cur:
        cur.execute("""
            SELECT provider_key, sender, receiver, reply_text, state
            FROM public.whatsapp_reply_outbox
            WHERE provider_key = %s
            FOR UPDATE
        """, (provider_key,))

        existing = cur.fetchone()

    if existing is not None:
        if isinstance(existing, dict):
            expected = (
                existing["sender"],
                existing["receiver"],
                existing["reply_text"],
            )
        else:
            expected = (
                existing[1],
                existing[2],
                existing[3],
            )

        if expected != (sender, receiver, body):
            raise ValueError("Outbox idempotency conflict")

        return {
            "status": "existing",
            "provider_key": provider_key,
        }

    inserted = stage_enqueue_reply(
        db,
        provider_key=provider_key,
        sender=sender,
        receiver=receiver,
        reply_text=body,
    )

    if not inserted:
        # A concurrent insert may have won the unique-key race.
        # Do not report success without verifying the record.
        with db.cursor() as cur:
            cur.execute("""
                SELECT sender, receiver, reply_text
                FROM public.whatsapp_reply_outbox
                WHERE provider_key = %s
            """, (provider_key,))
            collision = cur.fetchone()

        if collision is None:
            raise RuntimeError("Outbox collision could not be verified")

        if isinstance(collision, dict):
            actual = (
                collision["sender"],
                collision["receiver"],
                collision["reply_text"],
            )
        else:
            actual = tuple(collision)

        if actual != (sender, receiver, body):
            raise ValueError("Outbox provider-key collision")

        return {
            "status": "existing",
            "provider_key": provider_key,
        }

    return {
        "status": "staged",
        "provider_key": provider_key,
    }
