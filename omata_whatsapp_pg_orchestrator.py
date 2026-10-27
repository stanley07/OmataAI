"""Staging-only WhatsApp message proposal orchestrator.

No financial writes. No WhatsApp API calls.
Merchant identities must come from a verified selection.
"""

from voice import parse
from omata_whatsapp_pg_bridge import (
    BridgeValidationError,
    build_verified_sale_proposal,
)

TEST_MERCHANT = "OMATA_ATOMIC_TEST"


def prepare_whatsapp_sale(
    db,
    *,
    actor,
    merchant,
    provider_message_id,
    message_text,
    customer_id,
    product_id,
    location_id,
):
    if merchant != TEST_MERCHANT:
        raise BridgeValidationError("Staging merchant only")

    if not isinstance(actor, dict) or actor.get("merchant") != merchant:
        raise BridgeValidationError("Unauthorized merchant")

    if not isinstance(provider_message_id, str):
        raise BridgeValidationError("Missing message identity")

    if (
        not provider_message_id.strip()
        or len(provider_message_id) > 160
        or any(c.isspace() for c in provider_message_id)
    ):
        raise BridgeValidationError("Invalid message identity")

    if not isinstance(message_text, str) or not message_text.strip():
        raise BridgeValidationError("Empty message")

    if len(message_text) > 2000:
        raise BridgeValidationError("Message too long")

    parsed = parse(message_text, "sale")

    if parsed.get("kind") != "sale":
        raise BridgeValidationError("Not a clear sale")

    # This is a proposal identifier, not a financial write.
    # The same provider ID produces the same source.
    source = "wa-staging:" + provider_message_id

    proposal = build_verified_sale_proposal(
        db,
        parsed=parsed,
        actor=actor,
        merchant=merchant,
        source=source,
        customer_id=customer_id,
        product_id=product_id,
        location_id=location_id,
        description=message_text,
    )

    return {
        "source": source,
        "merchant": merchant,
        "proposal": proposal,
        "requires_human_confirmation": True,
        "financial_write_performed": False,
    }
