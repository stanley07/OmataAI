"""PostgreSQL review-session service for synthetic Omata staging.

Caller owns the transaction. No commits, Meta calls or financial posting.
"""

import re
from datetime import datetime, timezone

from omata_whatsapp_pg_review_session import (
    STAGING_MERCHANT,
    prepare_session_command,
)

SESSION_PATTERN = re.compile(r"^[A-Z0-9]{6,24}$")

ACTION_STATUSES = {
    "confirm": "confirmation_requested",
    "correct": "correction_requested",
    "cancel": "cancellation_requested",
}


def create_review_session(
    db,
    *,
    session_id,
    merchant,
    sender,
    business_phone_id,
    entry_id,
    expires_at,
):
    if merchant != STAGING_MERCHANT:
        raise PermissionError("Staging merchant only")

    if not isinstance(session_id, str) or not SESSION_PATTERN.fullmatch(session_id):
        raise ValueError("Invalid session ID")

    if not all(isinstance(x, str) and x.strip() for x in (
        sender, business_phone_id, entry_id
    )):
        raise ValueError("Missing session identity")

    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        raise ValueError("Timezone-aware expiration required")

    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("Expiration must be in the future")

    # Verify the exact sale belongs to the staging merchant and is still a draft.
    with db.cursor() as cur:
        cur.execute("""
            SELECT id
            FROM public.entries
            WHERE id = %s
              AND merchant = %s
              AND status = 'draft'
        """, (entry_id, merchant))

        if cur.fetchone() is None:
            raise ValueError("Active merchant draft not found")

        cur.execute("""
            INSERT INTO public.whatsapp_review_sessions
                (session_id, merchant, sender, business_phone_id,
                 entry_id, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING session_id
        """, (
            session_id, merchant, sender, business_phone_id,
            entry_id, expires_at,
        ))

        created = cur.fetchone()

    if created is None:
        raise RuntimeError("Review session creation failed")

    return {
        "session_id": session_id,
        "status": "awaiting_confirmation",
        "created": True,
    }


def consume_review_session(
    db,
    *,
    session_id,
    message_text,
    authenticated_sender,
    business_phone_id,
    merchant,
    entry_id,
):
    if merchant != STAGING_MERCHANT:
        raise PermissionError("Staging merchant only")

    if not isinstance(session_id, str) or not SESSION_PATTERN.fullmatch(session_id):
        raise ValueError("Invalid session reference")

    with db.cursor() as cur:
        cur.execute("""
            SELECT session_id, merchant, sender, business_phone_id,
                   entry_id, expires_at, status
            FROM public.whatsapp_review_sessions
            WHERE session_id = %s
            FOR UPDATE NOWAIT
        """, (session_id,))

        row = cur.fetchone()

    if row is None:
        raise ValueError("Review session not found")

    columns = (
        "session_id", "merchant", "sender", "business_phone_id",
        "entry_id", "expires_at", "status",
    )

    session = row if isinstance(row, dict) else dict(zip(columns, row))

    expires_at = session["expires_at"]

    if isinstance(expires_at, str):
        expiry_text = expires_at
    else:
        expiry_text = expires_at.isoformat()

    validation_session = {
        **session,
        "expires_at": expiry_text,
    }

    decision = prepare_session_command(
        session=validation_session,
        message_text=message_text,
        authenticated_sender=authenticated_sender,
        business_phone_id=business_phone_id,
        merchant=merchant,
        entry_id=entry_id,
    )

    action = decision["action"]
    new_status = ACTION_STATUSES.get(action)

    if new_status is None:
        raise ValueError("Unsupported review action")

    with db.cursor() as cur:
        cur.execute("""
            UPDATE public.whatsapp_review_sessions
            SET status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
              AND merchant = %s
              AND status = 'awaiting_confirmation'
              AND expires_at > CURRENT_TIMESTAMP
            RETURNING session_id
        """, (new_status, session_id, merchant))

        updated = cur.fetchone()

    if updated is None:
        raise ValueError("Review session already used or expired")

    return {
        "session_id": session_id,
        "entry_id": entry_id,
        "merchant": merchant,
        "action": action,
        "status": new_status,
        "session_consumed": True,
        "financial_write_performed": False,
        "confirmed": False,
    }
