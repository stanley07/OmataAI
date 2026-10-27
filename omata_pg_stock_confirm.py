"""Approval-controlled PostgreSQL stock confirmation.

Caller owns database connection and commit/rollback.

All inventory mutations must use the same per-merchant,
per-product advisory lock protocol, including transfers,
inventory-linked sales and reversals.
"""

import uuid
from decimal import Decimal, ROUND_HALF_UP

from omata_pg_stock_review_token import verify_token
from omata_pg_stock_review import review_stock_draft

from omata_pg_inventory import (
    require_actor,
    branch_stock_balance,
    InventoryValidationError,
)

MANAGEMENT_ROLES = {"owner", "admin", "manager"}
INCOMING = {"opening", "received"}
OUTGOING = {"damaged", "expired", "lost"}


def confirm_stock_draft(
    db,
    *,
    actor,
    merchant,
    draft_id,
    human_confirmed=False,
    review_token=None,
    review_secret=None,
):
    authenticated_merchant = require_actor(actor, writable=True)

    if merchant != authenticated_merchant:
        raise PermissionError("Cross-merchant confirmation refused.")

    if actor.get("role") not in MANAGEMENT_ROLES:
        raise PermissionError("Management authorization required.")

    if human_confirmed is not True:
        raise PermissionError("Explicit human confirmation required.")

    if not review_token or not review_secret:
        raise PermissionError(
            "A valid server-issued stock review token is required."
        )

    draft_id = str(draft_id or "").strip()

    if not draft_id:
        raise InventoryValidationError("Stock draft ID required.")

    with db.cursor() as cur:

        # Read the immutable identity needed to acquire the shared lock.
        # Revalidate the record after obtaining that lock.
        cur.execute(
            """
            SELECT merchant, product
            FROM stock_moves
            WHERE id=%s AND merchant=%s
            """,
            (draft_id, merchant),
        )

        identity = cur.fetchone()

        if identity is None:
            raise InventoryValidationError(
                "Stock draft not found in this business."
            )

        product_id = identity["product"]

        # Transaction-scoped serialization across all locations for
        # the same merchant/product. Every future inventory writer
        # must use the identical lock protocol.
        cur.execute(
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended(%s, 0)
            )
            """,
            (merchant + "\x1f" + product_id,),
        )

        cur.execute(
            """
            SELECT *
            FROM stock_moves
            WHERE id=%s AND merchant=%s
            FOR UPDATE
            """,
            (draft_id, merchant),
        )

        movement = cur.fetchone()

        if movement is None or movement["product"] != product_id:
            raise InventoryValidationError(
                "Stock movement changed during confirmation."
            )

        if movement["entry_id"]:
            raise InventoryValidationError(
                "Sale-linked stock must be approved with its sale."
            )

        # Verify against authoritative draft details while the
        # database row and product advisory lock are held.
        reviewed = review_stock_draft(
            db,
            actor=actor,
            draft_id=draft_id,
        )

        verify_token(
            token=review_token,
            draft=reviewed,
            actor=actor,
            secret=review_secret,
        )

        if movement["status"] == "confirmed":
            return {
                "id": draft_id,
                "status": "confirmed",
                "existing": True,
            }

        if movement["status"] != "draft":
            raise InventoryValidationError(
                "Only active stock drafts may be confirmed."
            )

        kind = movement["kind"]

        if kind not in INCOMING | OUTGOING:
            raise InventoryValidationError(
                "Unsupported stock confirmation type."
            )

        location_id = movement["location_id"]

        if not location_id:
            raise InventoryValidationError(
                "Stock draft must specify an existing location."
            )

        balance = branch_stock_balance(
            db,
            actor=actor,
            product_id=product_id,
            location_id=location_id,
        )

        current_quantity = balance["quantity"]
        delta = int(movement["quantity"])

        if delta == 0:
            raise InventoryValidationError(
                "Zero-quantity stock movements are not allowed."
            )

        if kind in INCOMING:
            if delta <= 0:
                raise InventoryValidationError(
                    "Incoming stock must increase quantity."
                )

            cost = movement["cost_total"]

            if cost is None or int(cost) <= 0:
                raise InventoryValidationError(
                    "Incoming stock requires positive purchase cost."
                )

            if kind == "opening" and current_quantity != 0:
                raise InventoryValidationError(
                    "Opening stock requires zero existing stock."
                )

        else:
            if delta >= 0:
                raise InventoryValidationError(
                    "Outgoing stock must decrease quantity."
                )

            if current_quantity + delta < 0:
                raise InventoryValidationError(
                    "Insufficient stock at selected location."
                )

            # Cost basis: reconstruct net confirmed inventory value
            # for the location from stock movements.
            #
            # Transfers require separate valuation integration.
            # Refuse outgoing confirmation when transfers already
            # exist, rather than risk an incorrect financial value.
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM stock_transfers
                WHERE merchant=%s AND product=%s
                  AND status='confirmed'
                  AND (from_location=%s OR to_location=%s)
                """,
                (merchant, product_id, location_id, location_id),
            )

            if cur.fetchone()["n"]:
                raise InventoryValidationError(
                    "Transfer-aware inventory costing is not yet enabled."
                )

            cur.execute(
                """
                SELECT quantity, cost_total, kind
                FROM stock_moves
                WHERE merchant=%s
                  AND product=%s
                  AND location_id=%s
                  AND status='confirmed'
                ORDER BY created, id
                """,
                (merchant, product_id, location_id),
            )

            quantity_total = 0
            value_total = 0

            for previous in cur.fetchall():
                previous_q = int(previous["quantity"])
                previous_cost = previous["cost_total"]

                if previous_q == 0 or previous_cost is None:
                    raise InventoryValidationError(
                        "Existing inventory cost basis is incomplete."
                    )

                previous_cost = int(previous_cost)

                if previous_q > 0:
                    if previous_cost <= 0:
                        raise InventoryValidationError(
                            "Incoming stock cost is invalid."
                        )
                    quantity_total += previous_q
                    value_total += previous_cost
                else:
                    if previous_cost < 0:
                        raise InventoryValidationError(
                            "Outgoing stock cost is invalid."
                        )
                    quantity_total += previous_q
                    value_total -= previous_cost

                if quantity_total < 0 or value_total < 0:
                    raise InventoryValidationError(
                        "Historical inventory valuation is inconsistent."
                    )

            if quantity_total != current_quantity:
                raise InventoryValidationError(
                    "Inventory balance and valuation do not reconcile."
                )

            if quantity_total <= 0 or value_total <= 0:
                raise InventoryValidationError(
                    "Insufficient valued stock."
                )

            cost = int(
                (
                    Decimal(value_total)
                    * Decimal(abs(delta))
                    / Decimal(quantity_total)
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )

            if cost > value_total:
                raise InventoryValidationError(
                    "Calculated stock cost exceeds inventory value."
                )

        cur.execute(
            """
            UPDATE stock_moves
            SET status='confirmed', cost_total=%s
            WHERE id=%s AND merchant=%s AND status='draft'
            RETURNING id
            """,
            (cost, draft_id, merchant),
        )

        if cur.fetchone() is None:
            raise InventoryValidationError(
                "Stock draft was not available for confirmation."
            )

        cur.execute(
            """
            INSERT INTO audit_log
                (id,merchant,user_id,role,action,
                 target_type,target_id,details)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                str(uuid.uuid4()),
                merchant,
                actor["id"],
                actor["role"],
                "stock.confirm",
                "stock_move",
                draft_id,
                '{"human_confirmed":true}',
            ),
        )

        return {
            "id": draft_id,
            "status": "confirmed",
            "existing": False,
            "quantity_change": delta,
            "cost_total": int(cost),
        }
