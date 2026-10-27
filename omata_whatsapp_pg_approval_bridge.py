"""Staging PostgreSQL WhatsApp approval bridge.

The caller MUST supply a context established by a verified Meta webhook
and trusted merchant routing. This module does not verify signatures.

No financial confirmation, commits or outbound Meta calls.
"""

from omata_whatsapp_pg_session_store import consume_review_session

STAGING_MERCHANT = "OMATA_ATOMIC_TEST"


def process_verified_approval(
    db,
    *,
    verified_context,
    session_id,
    message_text,
    entry_id,
):
    if not isinstance(verified_context, dict):
        raise PermissionError("Verified webhook context required")

    if verified_context.get("signature_verified") is not True:
        raise PermissionError("Meta signature not verified")

    if verified_context.get("merchant_route_verified") is not True:
        raise PermissionError("Merchant route not verified")

    merchant = verified_context.get("merchant")
    sender = verified_context.get("sender")
    business_phone_id = verified_context.get("business_phone_id")

    if merchant != STAGING_MERCHANT:
        raise PermissionError("Staging merchant only")

    if not isinstance(sender, str) or not sender.strip():
        raise PermissionError("Verified sender missing")

    if not isinstance(business_phone_id, str) or not business_phone_id.strip():
        raise PermissionError("Verified business phone ID missing")

    result = consume_review_session(
        db,
        session_id=session_id,
        message_text=message_text,
        authenticated_sender=sender,
        business_phone_id=business_phone_id,
        merchant=merchant,
        entry_id=entry_id,
    )

    return {
        **result,
        "webhook_verified": True,
        "merchant_route_verified": True,
        "financial_confirmation_executed": False,
        "whatsapp_reply_sent": False,
    }
