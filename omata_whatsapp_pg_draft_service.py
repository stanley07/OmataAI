"""Isolated PostgreSQL WhatsApp draft service.

Only the synthetic staging merchant is supported.
Callers manage the transaction and its final commit/rollback.
No financial confirmation occurs here.
"""

import uuid

from omata_whatsapp_pg_orchestrator import prepare_whatsapp_sale
from omata_pg_atomic_sale import create_atomic_sale_draft
from omata_whatsapp_pg_bridge import BridgeValidationError

TEST_MERCHANT = "OMATA_ATOMIC_TEST"


def stage_whatsapp_sale_draft(
    db,
    *,
    actor,
    merchant,
    provider_message_id,
    sender,
    receiver,
    message_text,
    customer_id,
    product_id,
    location_id,
):
    """Stage linked inbox and sale draft in the caller's transaction."""

    if merchant != TEST_MERCHANT:
        raise BridgeValidationError("Staging merchant only")

    if not isinstance(actor, dict) or actor.get("merchant") != merchant:
        raise BridgeValidationError("Unauthorized merchant")

    if not isinstance(sender, str) or not sender.strip():
        raise BridgeValidationError("Missing sender")

    if not isinstance(receiver, str) or not receiver.strip():
        raise BridgeValidationError("Missing receiver")

    result = prepare_whatsapp_sale(
        db,
        actor=actor,
        merchant=merchant,
        provider_message_id=provider_message_id,
        message_text=message_text,
        customer_id=customer_id,
        product_id=product_id,
        location_id=location_id,
    )

    payload = result["proposal"]["payload"]

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, merchant, sender, receiver, body
            FROM public.inbox
            WHERE provider_id=%s
            """,
            (provider_message_id,),
        )
        existing = cur.fetchone()

        if existing is not None:
            if (
                existing["merchant"] != merchant
                or existing["sender"] != sender
                or existing["receiver"] != receiver
                or existing["body"] != message_text
            ):
                raise BridgeValidationError(
                    "Provider message ID conflicts with existing inbox"
                )

            cur.execute(
                """
                SELECT target_kind, target_id
                FROM public.inbox_claims
                WHERE inbox_id=%s
                """,
                (existing["id"],),
            )
            claim = cur.fetchone()

            if claim is None or claim["target_kind"] != "sale":
                raise BridgeValidationError(
                    "Existing inbox has no matching sale claim"
                )

            cur.execute(
                """
                SELECT id, merchant, source, status
                FROM public.entries
                WHERE id=%s AND merchant=%s
                """,
                (claim["target_id"], merchant),
            )
            entry = cur.fetchone()

            if entry is None or entry["source"] != payload["source"]:
                raise BridgeValidationError(
                    "Existing claim does not match financial source"
                )

            return {
                "status": "existing",
                "entry_id": entry["id"],
                "entry_status": entry["status"],
                "source": payload["source"],
                "requires_human_confirmation": (
                    entry["status"] != "confirmed"
                ),
                "created": False,
            }

        inbox_id = str(uuid.uuid4())

        cur.execute(
            """
            INSERT INTO public.inbox
                (id, merchant, provider_id, sender,
                 receiver, body, channel)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                inbox_id,
                merchant,
                provider_message_id,
                sender,
                receiver,
                message_text,
                "whatsapp",
            ),
        )

    draft = create_atomic_sale_draft(
        db,
        actor=actor,
        payload=payload,
    )

    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.inbox_claims
                (inbox_id, target_kind, target_id)
            VALUES (%s,'sale',%s)
            """,
            (inbox_id, draft["entry_id"]),
        )

    return {
        "status": "draft",
        "entry_id": draft["entry_id"],
        "stock_move_id": draft["stock_move_id"],
        "source": payload["source"],
        "requires_human_confirmation": True,
        "created": True,
    }
