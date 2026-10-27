"""Omata AI staging review + outbound queue coordinator.

Caller owns the PostgreSQL connection and outer transaction.
No WhatsApp API calls or financial confirmations.
"""

from omata_pg_pilot_review import stage_pilot_review
from omata_whatsapp_pg_review_message import prepare_review_message
from omata_whatsapp_pg_delivery import build_review_delivery
from omata_whatsapp_pg_guarded_outbox import guarded_enqueue_review


class PilotOutboxError(Exception):
    pass


def stage_pilot_review_outbox(
    db, *,
    request,
    review_secret,
):
    """Stage a draft, review session and guarded outbound response."""

    if not isinstance(request, dict):
        raise PilotOutboxError("Invalid request")

    actor = request.get("actor")
    merchant = request.get("merchant")
    sender = request.get("sender")
    receiver = request.get("receiver")

    if not isinstance(actor, dict):
        raise PilotOutboxError("Invalid actor")

    if actor.get("merchant") != merchant:
        raise PilotOutboxError("Actor merchant mismatch")

    if not isinstance(review_secret, str) or not review_secret:
        raise PilotOutboxError("Review secret required")

    # Nested transaction provides a savepoint if the caller
    # already owns an active PostgreSQL transaction.
    with db.transaction():

        staged = stage_pilot_review(db, request=request)

        if (
            staged.get("status") != "awaiting_confirmation"
            or staged.get("requires_human_confirmation") is not True
        ):
            raise PilotOutboxError("Review session not ready")

        entry_id = staged["entry_id"]

        prepared = prepare_review_message(
            db,
            actor=actor,
            merchant=merchant,
            entry_id=entry_id,
            review_secret=review_secret,
        )

        if (
            not isinstance(prepared, dict)
            or prepared.get("requires_human_confirmation") is not True
            or prepared.get("confirmed") is not False
            or prepared.get("sent") is not False
        ):
            raise PilotOutboxError("Unsafe review message")

        delivery = build_review_delivery(
            merchant=merchant,
            recipient=sender,
            phone_number_id=receiver,
            review_result=prepared,
        )

        if (
            not isinstance(delivery, dict)
            or delivery.get("dry_run") is not True
            or delivery.get("sent") is not False
            or delivery.get("requires_human_confirmation") is not True
        ):
            raise PilotOutboxError("Unsafe outbound delivery")

        queued = guarded_enqueue_review(
            db,
            merchant=merchant,
            entry_id=entry_id,
            delivery=delivery,
        )

        if (
            not isinstance(queued, dict)
            or not queued.get("provider_key")
        ):
            raise PilotOutboxError("Outbound queue failed")

        return {
            "status": "awaiting_confirmation",
            "entry_id": entry_id,
            "session_id": staged["session_id"],
            "outbox_provider_key": queued["provider_key"],
            "outbox_status": queued.get("status"),
            "requires_human_confirmation": True,
            "financially_confirmed": False,
            "whatsapp_sent": False,
        }
