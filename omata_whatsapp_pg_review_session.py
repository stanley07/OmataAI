"""Staging-only WhatsApp review-session validation.

Pure Python. No database writes, Meta calls or financial confirmation.
"""

from datetime import datetime, timezone


STAGING_MERCHANT = "OMATA_ATOMIC_TEST"


def validate_review_session(
    *,
    session,
    authenticated_sender,
    business_phone_id,
    merchant,
    entry_id,
    now=None,
):
    if merchant != STAGING_MERCHANT:
        raise PermissionError("Staging merchant only")

    if not isinstance(session, dict):
        raise ValueError("Review session required")

    required = (
        "session_id",
        "merchant",
        "sender",
        "business_phone_id",
        "entry_id",
        "expires_at",
        "status",
    )

    if any(not session.get(field) for field in required):
        raise ValueError("Incomplete review session")

    if session["merchant"] != merchant:
        raise PermissionError("Session merchant mismatch")

    if not authenticated_sender or authenticated_sender != session["sender"]:
        raise PermissionError("Sender mismatch")

    if not business_phone_id or business_phone_id != session["business_phone_id"]:
        raise PermissionError("Business phone mismatch")

    if not entry_id or entry_id != session["entry_id"]:
        raise PermissionError("Sale reference mismatch")

    if session["status"] != "awaiting_confirmation":
        raise ValueError("Review session is not active")

    current_time = now or datetime.now(timezone.utc)

    if current_time.tzinfo is None:
        raise ValueError("Current time must include timezone")

    expires_at = datetime.fromisoformat(
        session["expires_at"].replace("Z", "+00:00")
    )

    if expires_at.tzinfo is None:
        raise ValueError("Expiration must include timezone")

    if current_time >= expires_at:
        raise PermissionError("Review session expired")

    return {
        "valid": True,
        "session_id": session["session_id"],
        "entry_id": entry_id,
        "merchant": merchant,
        "requires_signed_review": True,
        "confirmed": False,
    }


def prepare_session_command(
    *,
    session,
    message_text,
    authenticated_sender,
    business_phone_id,
    merchant,
    entry_id,
    now=None,
):
    from omata_whatsapp_pg_approval import (
        prepare_approval_decision,
    )

    validation = validate_review_session(
        session=session,
        authenticated_sender=authenticated_sender,
        business_phone_id=business_phone_id,
        merchant=merchant,
        entry_id=entry_id,
        now=now,
    )

    if not isinstance(message_text, str):
        raise ValueError("Message text required")

    # Every actionable command must explicitly identify its session.
    parts = message_text.strip().split()

    if len(parts) != 2:
        raise ValueError("Explicit session reference required")

    command, session_id = parts

    if session_id != validation["session_id"]:
        raise PermissionError("Incorrect review-session reference")

    decision = prepare_approval_decision(
        message_text=command,
        authenticated_sender=authenticated_sender,
        expected_sender=session["sender"],
        merchant=merchant,
        entry_id=entry_id,
    )

    if decision["status"] == "unrecognized":
        raise ValueError("Unsupported review command")

    return {
        **decision,
        "session_id": session_id,
        "session_validated": True,
        "confirmed": False,
        "financial_write_performed": False,
    }
