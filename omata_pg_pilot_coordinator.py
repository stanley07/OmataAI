"""Omata AI PostgreSQL pilot coordinator.

Caller owns the PostgreSQL transaction.
Staging merchant restrictions remain enforced.
No financial confirmation or outbound sending.
"""

from omata_whatsapp_pg_safe_drafts import (
    stage_safe_whatsapp_draft,
)


class PilotIntegrationError(Exception):
    pass


def stage_pilot_draft(db, *, request):
    """Stage a WhatsApp sale draft using existing safety guards."""

    if not isinstance(request, dict):
        raise PilotIntegrationError("Request must be a dictionary")

    required = (
        "actor",
        "merchant",
        "provider_message_id",
        "sender",
        "receiver",
        "message_text",
        "customer_id",
        "product_id",
        "location_id",
    )

    missing = [
        name for name in required
        if name not in request
    ]

    if missing:
        raise PilotIntegrationError(
            "Missing required fields: " + ", ".join(missing)
        )

    if not isinstance(request["actor"], dict):
        raise PilotIntegrationError("Invalid actor")

    if request["actor"].get("merchant") != request["merchant"]:
        raise PilotIntegrationError("Actor merchant mismatch")

    result = stage_safe_whatsapp_draft(
        db,
        **{name: request[name] for name in required},
    )

    if not isinstance(result, dict):
        raise PilotIntegrationError("Invalid draft service response")

    if result.get("retry_required"):
        return result

    if result.get("requires_human_confirmation") is not True:
        raise PilotIntegrationError(
            "Draft must require human confirmation"
        )

    if result.get("status") not in ("draft", "existing"):
        raise PilotIntegrationError(
            "Unexpected draft status"
        )

    return result
