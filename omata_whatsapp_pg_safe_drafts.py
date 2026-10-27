"""Staging-only safe WhatsApp draft orchestration.

The caller owns the connection and outer transaction.
No commits, confirmations or external messages.
"""

import psycopg

from omata_whatsapp_pg_bridge import BridgeValidationError
from omata_whatsapp_pg_draft_service import stage_whatsapp_sale_draft

TEST_MERCHANT = "OMATA_ATOMIC_TEST"


def _verify_existing_selection(db, *, merchant, provider_id,
                               customer_id, product_id, location_id):
    """Reject a replay that changes the original financial selections."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT i.id AS inbox_id,
                   c.target_kind,
                   e.id AS entry_id,
                   e.customer,
                   e.location_id,
                   e.source,
                   s.product,
                   s.location_id AS stock_location
            FROM public.inbox i
            LEFT JOIN public.inbox_claims c
              ON c.inbox_id = i.id
            LEFT JOIN public.entries e
              ON e.id = c.target_id AND e.merchant = i.merchant
            LEFT JOIN public.stock_moves s
              ON s.entry_id = e.id AND s.merchant = e.merchant
            WHERE i.provider_id = %s AND i.merchant = %s
        """, (provider_id, merchant))

        existing = cur.fetchone()

    if existing is None:
        return

    expected_source = "wa-staging:" + provider_id

    if (
        existing["target_kind"] != "sale"
        or existing["entry_id"] is None
        or existing["source"] != expected_source
        or existing["customer"] != customer_id
        or existing["product"] != product_id
        or existing["location_id"] != location_id
        or existing["stock_location"] != location_id
    ):
        raise BridgeValidationError(
            "Existing message conflicts with financial selections"
        )


def stage_safe_whatsapp_draft(db, **kwargs):
    """Stage a draft, returning retryable for concurrent conflicts.

    Caller must supply a transaction-scoped PostgreSQL connection
    configured with dict_row and must decide whether to commit.
    """

    merchant = kwargs.get("merchant")
    actor = kwargs.get("actor")
    provider_id = kwargs.get("provider_message_id")

    if (
        merchant != TEST_MERCHANT
        or not isinstance(actor, dict)
        or actor.get("merchant") != TEST_MERCHANT
    ):
        raise BridgeValidationError("Staging merchant authorization failed")

    # Validate message and source before constructing any SQL.
    if (
        not isinstance(provider_id, str)
        or not provider_id.strip()
        or len(provider_id) > 160
        or any(c.isspace() for c in provider_id)
    ):
        raise BridgeValidationError("Invalid provider message ID")

    # Nested transaction creates a savepoint. A database constraint
    # error can be recovered without losing the outer transaction.
    try:
        with db.transaction():
            _verify_existing_selection(
                db,
                merchant=merchant,
                provider_id=provider_id,
                customer_id=kwargs.get("customer_id"),
                product_id=kwargs.get("product_id"),
                location_id=kwargs.get("location_id"),
            )

            result = stage_whatsapp_sale_draft(db, **kwargs)

            _verify_existing_selection(
                db,
                merchant=merchant,
                provider_id=provider_id,
                customer_id=kwargs.get("customer_id"),
                product_id=kwargs.get("product_id"),
                location_id=kwargs.get("location_id"),
            )

            return result

    except (
        psycopg.errors.UniqueViolation,
        psycopg.errors.LockNotAvailable,
        psycopg.errors.QueryCanceled,
    ):
        return {
            "status": "retryable",
            "created": False,
            "requires_human_confirmation": True,
            "retry_required": True,
            "entry_id": None,
        }
