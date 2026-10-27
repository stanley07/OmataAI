"""Staging-only atomic inbound WhatsApp approval processing.

The caller owns the transaction. No commits, Meta calls or financial posting.
"""

import uuid

from omata_whatsapp_pg_approval_bridge import process_verified_approval

STAGING_MERCHANT = "OMATA_ATOMIC_TEST"


def process_idempotent_approval(
    db,
    *,
    authenticated_message,
    session_id,
    entry_id,
):
    if not isinstance(authenticated_message, dict):
        raise PermissionError("Authenticated message required")

    context = authenticated_message.get("verified_context")

    if not isinstance(context, dict):
        raise PermissionError("Verified context missing")

    if context.get("signature_verified") is not True:
        raise PermissionError("Signature verification required")

    if context.get("merchant_route_verified") is not True:
        raise PermissionError("Merchant authorization required")

    merchant = context.get("merchant")
    sender = context.get("sender")
    receiver = context.get("business_phone_id")

    if merchant != STAGING_MERCHANT:
        raise PermissionError("Staging merchant only")

    provider_id = authenticated_message.get("provider_message_id")
    message_text = authenticated_message.get("message_text")

    if not (
        isinstance(provider_id, str)
        and provider_id.startswith("wamid.")
        and 6 < len(provider_id) <= 2000
    ):
        raise ValueError("Invalid provider message ID")

    if not (
        isinstance(sender, str) and sender.isdigit()
        and isinstance(receiver, str) and receiver.isdigit()
        and isinstance(message_text, str) and message_text.strip()
    ):
        raise ValueError("Invalid authenticated message fields")

    # Insert the message before consuming its approval session.
    # The database unique constraint on provider_id provides
    # concurrency-safe replay detection.
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO public.inbox
                (id, merchant, provider_id, sender,
                 receiver, body, channel, created)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s,
                 CURRENT_TIMESTAMP::text)
            ON CONFLICT (provider_id) DO NOTHING
            RETURNING id
        """, (
            str(uuid.uuid4()),
            merchant,
            provider_id,
            sender,
            receiver,
            message_text,
            "whatsapp",
        ))

        row = cur.fetchone()

    if row is None:
        return {
            "status": "duplicate",
            "provider_message_id": provider_id,
            "processed": False,
            "financial_confirmation_executed": False,
        }

    # Session mutation and inbox insertion share the caller's
    # PostgreSQL transaction.
    decision = process_verified_approval(
        db,
        verified_context=context,
        session_id=session_id,
        message_text=message_text,
        entry_id=entry_id,
    )

    return {
        **decision,
        "provider_message_id": provider_id,
        "processed": True,
        "inbox_record_created": True,
    }
