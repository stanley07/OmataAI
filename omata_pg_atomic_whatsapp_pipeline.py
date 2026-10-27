"""Omata AI atomic staging WhatsApp pipeline.

The existing draft service owns inbox creation, sale/stock
drafts and inbox claims.

Caller owns the PostgreSQL transaction.
No Meta sending or financial confirmation.
"""

from omata_pg_atomic_inbox import stage_atomic_inbox
from omata_pg_pilot_review_outbox import stage_pilot_review_outbox


class AtomicWhatsAppPipelineError(Exception):
    pass


def stage_atomic_whatsapp_pipeline(db, *, request, review_secret):
    if not isinstance(request, dict):
        raise AtomicWhatsAppPipelineError("Invalid request")

    required = (
        "actor", "merchant", "provider_message_id",
        "sender", "receiver", "message_text",
        "customer_id", "product_id", "location_id",
    )

    for name in required:
        if name not in request:
            raise AtomicWhatsAppPipelineError(
                "Missing required field: " + name
            )

    actor = request["actor"]
    merchant = request["merchant"]

    if not isinstance(actor, dict) or actor.get("merchant") != merchant:
        raise AtomicWhatsAppPipelineError("Actor merchant mismatch")

    if not isinstance(review_secret, str) or len(review_secret) < 32:
        raise AtomicWhatsAppPipelineError("Invalid review secret")

    provider_id = request["provider_message_id"]
    sender = request["sender"]
    receiver = request["receiver"]
    body = request["message_text"]

    for name, value in (
        ("merchant", merchant),
        ("provider_message_id", provider_id),
        ("sender", sender),
        ("receiver", receiver),
        ("message_text", body),
    ):
        if not isinstance(value, str) or not value.strip():
            raise AtomicWhatsAppPipelineError("Invalid " + name)

    # The staging draft service owns the inbox insert.
    # Check existing delivery identity without writing anything.
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT i.id, i.merchant, i.sender, i.receiver,
                       i.body, c.target_kind, c.target_id
                FROM public.inbox i
                LEFT JOIN public.inbox_claims c
                  ON c.inbox_id = i.id
                WHERE i.provider_id = %s
                FOR UPDATE OF i
                """,
                (provider_id,),
            )
            existing = cur.fetchall()

        if existing:
            if len(existing) != 1:
                raise AtomicWhatsAppPipelineError(
                    "Ambiguous existing inbox claim"
                )

            row = existing[0]

            if (
                row["merchant"] != merchant
                or row["sender"] != sender
                or row["receiver"] != receiver
                or row["body"] != body
            ):
                raise AtomicWhatsAppPipelineError(
                    "Conflicting provider message identity"
                )

            if (
                row["target_kind"] != "sale"
                or not row["target_id"]
            ):
                raise AtomicWhatsAppPipelineError(
                    "Existing inbox lacks a valid sale claim"
                )

            # A replay must never create another draft,
            # session, review token, or outbound message.
            return {
                "status": "duplicate",
                "inbox_id": row["id"],
                "created": False,
                "financially_confirmed": False,
                "whatsapp_sent": False,
            }

        # This service creates the inbox record, sale,
        # stock draft, and inbox claim as one unit.
        result = stage_pilot_review_outbox(
            db,
            request=request,
            review_secret=review_secret,
        )

        if (
            not isinstance(result, dict)
            or result.get("status") != "awaiting_confirmation"
            or result.get("requires_human_confirmation") is not True
            or result.get("financially_confirmed") is not False
            or result.get("whatsapp_sent") is not False
        ):
            raise AtomicWhatsAppPipelineError(
                "Unsafe or incomplete review-outbox result"
            )

        # Verify that the draft service created the exact inbox
        # record. A new insert here indicates a broken contract.
        inbox = stage_atomic_inbox(
            db,
            merchant=merchant,
            provider_id=provider_id,
            sender=sender,
            receiver=receiver,
            body=body,
        )

        if inbox.get("created") is not False:
            raise AtomicWhatsAppPipelineError(
                "Draft service did not own inbox creation"
            )

        return {
            **result,
            "inbox_id": inbox["inbox_id"],
            "created": True,
        }
