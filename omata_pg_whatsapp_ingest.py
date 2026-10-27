"""Isolated PostgreSQL WhatsApp inbox adapter.

No outbound WhatsApp calls.
No financial ledger mutations.
No automatic confirmation.
"""

import os
import uuid
import psycopg


class WhatsAppIngestError(Exception):
    pass


def save_inbound_text(
    merchant,
    provider_id,
    sender,
    receiver,
    body,
    *,
    database_url=None,
):
    """Persist one merchant-scoped WhatsApp text message.

    Returns a dict containing inbox_id and created.
    """

    if not all(
        isinstance(value, str) and value.strip()
        for value in (merchant, provider_id, sender, receiver, body)
    ):
        raise WhatsAppIngestError("Invalid inbound message fields")

    if len(provider_id) > 500 or len(body) > 2000:
        raise WhatsAppIngestError("Message exceeds supported limits")

    url = database_url or os.environ.get("OMATA_PG_STAGING_URL")

    if not url:
        raise WhatsAppIngestError(
            "OMATA_PG_STAGING_URL is missing"
        )

    inbox_id = str(uuid.uuid4())

    with psycopg.connect(
        url,
        connect_timeout=10,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM public.shops
                WHERE id = %s
                """,
                (merchant,),
            )

            if cur.fetchone() is None:
                raise WhatsAppIngestError(
                    "Unknown merchant"
                )

            cur.execute(
                """
                INSERT INTO public.inbox (
                    id, merchant, provider_id,
                    sender, receiver, body, channel
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'whatsapp')
                ON CONFLICT (provider_id) DO NOTHING
                RETURNING id
                """,
                (
                    inbox_id,
                    merchant,
                    provider_id,
                    sender,
                    receiver,
                    body,
                ),
            )

            inserted = cur.fetchone()

            if inserted:
                return {
                    "inbox_id": inserted[0],
                    "created": True,
                }

            cur.execute(
                """
                SELECT id, merchant, sender, receiver
                FROM public.inbox
                WHERE provider_id = %s
                """,
                (provider_id,),
            )

            existing = cur.fetchone()

            if existing is None:
                raise WhatsAppIngestError(
                    "Message conflict could not be resolved"
                )

            if existing[1:] != (
                merchant, sender, receiver
            ):
                raise WhatsAppIngestError(
                    "Provider ID belongs to another route"
                )

            return {
                "inbox_id": existing[0],
                "created": False,
            }
