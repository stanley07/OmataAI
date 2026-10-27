
"""Isolated atomic-sale confirmation engine.

Not imported by app.py or WhatsApp.
Caller owns the PostgreSQL transaction and MUST rollback on error.
"""

import uuid

from omata_pg_inventory import branch_stock_balance
from omata_pg_sale_valuation import calculate_outgoing_cost
from omata_pg_sale_review import review_atomic_sale
from omata_pg_sale_review_token import verify_sale_token


def confirm_linked_sale(
    db,
    *,
    actor,
    merchant,
    entry_id,
    human_confirmed=False,
    review_token=None,
    review_secret=None,
):
    if not isinstance(actor, dict):
        raise PermissionError("Authentication required")

    if (
        not actor.get("id")
        or actor.get("merchant") != merchant
        or actor.get("must_change_password")
        or actor.get("role") not in ("owner", "admin")
    ):
        raise PermissionError("Management authorization required")

    if human_confirmed is not True:
        raise PermissionError("Explicit human approval required")

    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ValueError("Sale entry ID required")

    if not review_token or not review_secret:
        raise PermissionError("Signed sale review required")

    with db.cursor() as cur:
        # Shared financial serialization lock.
        cur.execute(
            "SELECT 1 FROM shops WHERE id=%s FOR UPDATE",
            (merchant,),
        )
        if cur.fetchone() is None:
            raise ValueError("Merchant not registered")

        cur.execute(
            """SELECT product FROM stock_moves
               WHERE merchant=%s AND entry_id=%s""",
            (merchant, entry_id),
        )
        identity = cur.fetchone()

        if identity is None:
            raise ValueError("Linked stock draft missing")

        product_id = identity["product"]

        # Serialize all stock writers for the same product.
        cur.execute(
            """SELECT pg_advisory_xact_lock(
                hashtextextended(%s, 0)
            )""",
            (merchant + "\x1f" + product_id,),
        )

        # The authoritative review queries now run under both locks.
        reviewed = review_atomic_sale(
            db,
            actor=actor,
            merchant=merchant,
            entry_id=entry_id,
            lock_rows=True,
        )

        if reviewed["stock_product"] != product_id:
            raise ValueError("Stock identity changed")

        verify_sale_token(
            token=review_token,
            sale=reviewed,
            actor=actor,
            secret=review_secret,
        )

        balance = branch_stock_balance(
            db,
            actor=actor,
            product_id=product_id,
            location_id=reviewed["location_id"],
        )

        quantity = abs(reviewed["stock_quantity"])

        if balance["quantity"] < quantity:
            raise ValueError("Insufficient inventory")

        # Outgoing valuation does not yet support transferred stock.
        if balance["transfer_quantity"] != 0:
            raise ValueError(
                "Transfer-aware sale valuation not enabled"
            )

        cur.execute(
            """SELECT COUNT(*) AS n FROM stock_transfers
               WHERE merchant=%s AND product=%s
                 AND status='confirmed'
                 AND (from_location=%s OR to_location=%s)""",
            (
                merchant,
                product_id,
                reviewed["location_id"],
                reviewed["location_id"],
            ),
        )

        if cur.fetchone()["n"]:
            raise ValueError("Transfer-aware stock valuation unavailable")

        cur.execute(
            """SELECT quantity, cost_total
               FROM stock_moves
               WHERE merchant=%s AND product=%s
                 AND location_id=%s AND status='confirmed'
               ORDER BY created, id""",
            (
                merchant,
                product_id,
                reviewed["location_id"],
            ),
        )

        history = cur.fetchall()

        cost = calculate_outgoing_cost(
            history,
            balance["quantity"],
            quantity,
        )

        cur.execute(
            """UPDATE entries
               SET status='confirmed',
                   confirmed=CURRENT_TIMESTAMP,
                   confirmed_by=%s
               WHERE merchant=%s AND id=%s
                 AND status='draft'
               RETURNING id""",
            (actor["id"], merchant, entry_id),
        )

        if cur.fetchone() is None:
            raise RuntimeError("Financial confirmation conflict")

        cur.execute(
            """UPDATE stock_moves
               SET status='confirmed', cost_total=%s
               WHERE merchant=%s AND entry_id=%s
                 AND status='draft'
               RETURNING id""",
            (cost, merchant, entry_id),
        )

        stock_row = cur.fetchone()

        if stock_row is None:
            raise RuntimeError("Inventory confirmation conflict")

        for action, target_type, target_id in (
            ("entry.confirm", "entry", entry_id),
            ("stock.confirm", "stock_move", stock_row["id"]),
        ):
            cur.execute(
                """INSERT INTO audit_log
                   (id,merchant,user_id,role,action,
                    target_type,target_id,details)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    str(uuid.uuid4()),
                    merchant,
                    actor["id"],
                    actor["role"],
                    action,
                    target_type,
                    target_id,
                    '{"human_confirmed":true,"atomic_sale":true}',
                ),
            )

        return {
            "entry_id": entry_id,
            "stock_move_id": stock_row["id"],
            "status": "confirmed",
            "customer": reviewed["customer"],
            "total": reviewed["total"],
            "paid": reviewed["paid"],
            "debt": reviewed["total"] - reviewed["paid"],
            "stock_quantity_change": reviewed["stock_quantity"],
            "inventory_cost": cost,
            "committed": False,
        }
