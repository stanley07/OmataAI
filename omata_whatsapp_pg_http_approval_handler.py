"""Staging-only authenticated WhatsApp approval transaction handler.

Caller owns the PostgreSQL transaction.
This module never commits, confirms a sale or sends WhatsApp messages.
"""

from omata_whatsapp_pg_http_adapter import (
    authenticate_inbound_approvals,
)
from omata_whatsapp_pg_approval_idempotency import (
    process_idempotent_approval,
)

STAGING_MERCHANT = "OMATA_ATOMIC_TEST"


def process_authenticated_webhook(
    db,
    *,
    raw_body,
    signature_header,
    app_secret,
    trusted_routes,
):
    """Process authenticated approval messages in a caller transaction."""

    approvals = authenticate_inbound_approvals(
        raw_body=raw_body,
        signature_header=signature_header,
        app_secret=app_secret,
        trusted_routes=trusted_routes,
    )

    results = []

    for approval in approvals:
        session_id = approval["session_id"]
        authenticated_message = approval["authenticated_message"]
        context = authenticated_message["verified_context"]

        merchant = context["merchant"]
        sender = context["sender"]
        business_phone_id = context["business_phone_id"]

        if merchant != STAGING_MERCHANT:
            raise PermissionError("Staging merchant only")

        # Classify an already-committed provider message before
        # checking the session's current state.
        provider_id = authenticated_message["provider_message_id"]

        with db.cursor() as cur:
            cur.execute("""
                SELECT merchant, sender, receiver, body
                FROM public.inbox
                WHERE provider_id = %s
            """, (provider_id,))
            prior = cur.fetchone()

        if prior is not None:
            columns = ("merchant", "sender", "receiver", "body")
            prior = (
                prior if isinstance(prior, dict)
                else dict(zip(columns, prior))
            )

            if (
                prior["merchant"] != merchant
                or prior["sender"] != sender
                or prior["receiver"] != business_phone_id
                or prior["body"] != authenticated_message["message_text"]
            ):
                raise PermissionError(
                    "Provider message ID identity collision"
                )

            results.append({
                "session_id": session_id,
                "status": "duplicate",
                "processed": False,
                "provider_message_id": provider_id,
            })
            continue

        # Resolve the exact draft from a persisted session.
        # The WhatsApp message cannot nominate an entry ID.
        with db.cursor() as cur:
            cur.execute("""
                SELECT entry_id
                FROM public.whatsapp_review_sessions
                WHERE session_id = %s
                  AND merchant = %s
                  AND sender = %s
                  AND business_phone_id = %s
                  AND status = 'awaiting_confirmation'
                  AND expires_at > CURRENT_TIMESTAMP
            """, (
                session_id,
                merchant,
                sender,
                business_phone_id,
            ))

            row = cur.fetchone()

        # A missing or invalid session must not mutate the inbox.
        if row is None:
            results.append({
                "session_id": session_id,
                "status": "rejected",
                "processed": False,
            })
            continue

        entry_id = (
            row["entry_id"]
            if isinstance(row, dict)
            else row[0]
        )

        result = process_idempotent_approval(
            db,
            authenticated_message=authenticated_message,
            session_id=session_id,
            entry_id=entry_id,
        )

        results.append(result)

    return {
        "authenticated_approval_count": len(approvals),
        "results": results,
        "financial_confirmation_executed": False,
        "whatsapp_reply_sent": False,
    }
