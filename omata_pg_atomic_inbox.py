"""Transaction-safe WhatsApp inbox persistence.

Uses a caller-owned PostgreSQL connection.
Does not open a new connection, commit, send or confirm.
"""

from uuid import uuid4


class AtomicInboxError(Exception):
    pass


def stage_atomic_inbox(
    db, *,
    merchant,
    provider_id,
    sender,
    receiver,
    body,
):
    fields = {
        "merchant": merchant,
        "provider_id": provider_id,
        "sender": sender,
        "receiver": receiver,
        "body": body,
    }

    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise AtomicInboxError("Invalid " + name)

    if len(provider_id) > 500 or len(body) > 2000:
        raise AtomicInboxError("Message exceeds supported length")

    inbox_id = str(uuid4())

    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM public.shops WHERE id = %s",
            (merchant,),
        )
        if cur.fetchone() is None:
            raise AtomicInboxError("Merchant not registered")

        cur.execute(
            """
            INSERT INTO public.inbox
                (id, merchant, provider_id, sender,
                 receiver, body, channel)
            VALUES (%s, %s, %s, %s, %s, %s, 'whatsapp')
            ON CONFLICT (provider_id) DO NOTHING
            RETURNING id
            """,
            (
                inbox_id, merchant, provider_id,
                sender, receiver, body,
            ),
        )

        inserted = cur.fetchone()

        if inserted is not None:
            return {
                "inbox_id": (
                    inserted["id"]
                    if isinstance(inserted, dict)
                    else inserted[0]
                ),
                "created": True,
            }

        cur.execute(
            """
            SELECT id, merchant, sender, receiver, body
            FROM public.inbox
            WHERE provider_id = %s
            FOR UPDATE
            """,
            (provider_id,),
        )

        existing = cur.fetchone()

        if existing is None:
            raise AtomicInboxError("Duplicate inbox record unavailable")

        if isinstance(existing, dict):
            stored_id = existing["id"]
            stored_merchant = existing["merchant"]
            stored_sender = existing["sender"]
            stored_receiver = existing["receiver"]
            stored_body = existing["body"]
        else:
            (
                stored_id,
                stored_merchant,
                stored_sender,
                stored_receiver,
                stored_body,
            ) = existing

        if (
            stored_merchant != merchant
            or stored_sender != sender
            or stored_receiver != receiver
            or stored_body != body
        ):
            raise AtomicInboxError(
                "Provider ID conflicts with another message"
            )

        return {
            "inbox_id": stored_id,
            "created": False,
        }
