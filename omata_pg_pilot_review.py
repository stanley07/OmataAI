"""Omata AI pilot draft + review-session integration.

Caller owns PostgreSQL transaction.
No outbound WhatsApp messages.
No financial confirmation.
"""

from datetime import datetime, timedelta, timezone
import hashlib

from omata_pg_pilot_coordinator import stage_pilot_draft
from omata_pg_pilot_session_replay import create_or_recover_review_session


class PilotReviewError(Exception):
    pass


def stage_pilot_review(db, *, request):
    if not isinstance(request, dict):
        raise PilotReviewError("Invalid request")

    required = (
        "merchant",
        "sender",
        "receiver",
        "provider_message_id",
    )

    for field in required:
        if not isinstance(request.get(field), str) or not request[field]:
            raise PilotReviewError("Missing or invalid " + field)

    # Caller must supply an existing, transaction-scoped connection.
    # A nested transaction is safe with psycopg transaction/savepoint handling.
    with db.transaction():

        draft = stage_pilot_draft(db, request=request)

        if draft.get("retry_required"):
            raise PilotReviewError(
                "Retry required; rolling back transaction"
            )

        entry_id = draft.get("entry_id")

        if (
            not isinstance(entry_id, str)
            or not entry_id
            or draft.get("requires_human_confirmation") is not True
        ):
            raise PilotReviewError(
                "Draft is not eligible for review"
            )


        identity = "\x1f".join((
            request["merchant"],
            request["receiver"],
            request["sender"],
            request["provider_message_id"],
        ))

        session_id = (
            "OM"
            + hashlib.sha256(identity.encode()).hexdigest()[:22].upper()
        )

        expires_at = datetime.now(
            timezone.utc
        ) + timedelta(minutes=30)

        session = create_or_recover_review_session(
            db,
            session_id=session_id,
            merchant=request["merchant"],
            sender=request["sender"],
            business_phone_id=request["receiver"],
            entry_id=entry_id,
            expires_at=expires_at,
        )

        if not isinstance(session, dict):
            raise PilotReviewError("Invalid review-session result")

        if session.get("status") != "awaiting_confirmation":
            raise PilotReviewError(
                "Unexpected review-session status"
            )

        return {
            "status": "awaiting_confirmation",
            "entry_id": entry_id,
            "session_id": session.get("session_id", session_id),
            "session_created": session.get("created", False),
            "requires_human_confirmation": True,
            "financially_confirmed": False,
        }
