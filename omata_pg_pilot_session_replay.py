"""Replay-safe wrapper for staging WhatsApp review sessions.

Uses PostgreSQL savepoints to recover from duplicate session IDs.
Does not confirm sales, alter stock, or send WhatsApp messages.
"""

from datetime import datetime, timezone
from psycopg.errors import UniqueViolation
from omata_whatsapp_pg_session_store import create_review_session


class ReviewReplayConflict(Exception):
    pass


def create_or_recover_review_session(
    db, *,
    session_id,
    merchant,
    sender,
    business_phone_id,
    entry_id,
    expires_at,
):
    arguments = dict(
        session_id=session_id,
        merchant=merchant,
        sender=sender,
        business_phone_id=business_phone_id,
        entry_id=entry_id,
        expires_at=expires_at,
    )

    # The savepoint prevents a duplicate-key exception from
    # aborting the caller's entire PostgreSQL transaction.
    try:
        with db.transaction():
            return create_review_session(db, **arguments)
    except UniqueViolation:
        pass

    # Verify existing session identity before accepting a replay.
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT merchant, sender, business_phone_id,
                   entry_id, status, expires_at
            FROM public.whatsapp_review_sessions
            WHERE session_id = %s
            FOR UPDATE
            """,
            (session_id,),
        )
        existing = cur.fetchone()

    if existing is None:
        raise ReviewReplayConflict(
            "Session conflict without recoverable record"
        )

    stored_merchant, stored_sender, stored_phone, (
        stored_entry
    ), stored_status, stored_expiry = existing

    if (
        stored_merchant != merchant
        or stored_sender != sender
        or stored_phone != business_phone_id
        or stored_entry != entry_id
    ):
        raise ReviewReplayConflict(
            "Existing review session identity mismatch"
        )

    if stored_status != "awaiting_confirmation":
        raise ReviewReplayConflict(
            "Existing review session is not awaiting confirmation"
        )

    if stored_expiry is None:
        raise ReviewReplayConflict(
            "Existing review session has no expiration"
        )

    if (
        isinstance(stored_expiry, datetime)
        and stored_expiry.tzinfo is not None
        and stored_expiry <= datetime.now(timezone.utc)
    ):
        raise ReviewReplayConflict(
            "Existing review session has expired"
        )

    return {
        "session_id": session_id,
        "status": "awaiting_confirmation",
        "created": False,
    }
