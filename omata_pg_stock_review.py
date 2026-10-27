"""Read-only PostgreSQL stock draft review.

Returns database-authoritative movement details.
No commits, stock changes, or SQLite fallback.
"""

from omata_pg_inventory import (
    require_actor,
    InventoryValidationError,
)

REVIEW_ROLES = {"owner", "admin", "manager"}


def review_stock_draft(db, *, actor, draft_id):
    merchant = require_actor(actor, writable=True)

    if actor.get("role") not in REVIEW_ROLES:
        raise PermissionError("Management review required.")

    draft_id = str(draft_id or "").strip()

    if not draft_id or len(draft_id) > 200:
        raise InventoryValidationError("Valid stock draft ID required.")

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT
                m.id,
                m.merchant,
                m.source,
                m.product,
                p.name AS product_name,
                p.unit,
                m.location_id,
                l.name AS location_name,
                m.kind,
                m.quantity,
                m.status,
                m.cost_total,
                m.supplier,
                s.name AS supplier_name,
                m.entry_id,
                m.created
            FROM stock_moves AS m
            JOIN products AS p
              ON p.merchant = m.merchant
             AND p.id = m.product
            JOIN locations AS l
              ON l.merchant = m.merchant
             AND l.id = m.location_id
            LEFT JOIN suppliers AS s
              ON s.merchant = m.merchant
             AND s.id = m.supplier
            WHERE m.id = %s
              AND m.merchant = %s
            """,
            (draft_id, merchant),
        )

        row = cur.fetchone()

        if row is None:
            raise InventoryValidationError(
                "Stock draft not found in this business."
            )

        if row["entry_id"]:
            raise InventoryValidationError(
                "Sale-linked inventory must be reviewed with its sale."
            )

        if row["status"] != "draft":
            raise InventoryValidationError(
                "Only active stock drafts can be reviewed for approval."
            )

        if row["kind"] not in {
            "opening", "received", "damaged", "expired", "lost"
        }:
            raise InventoryValidationError(
                "Unsupported stock review movement."
            )

        quantity = int(row["quantity"])

        if quantity == 0:
            raise InventoryValidationError(
                "Zero-quantity movement cannot be approved."
            )

        if row["kind"] in {"opening", "received"} and quantity <= 0:
            raise InventoryValidationError(
                "Incoming stock quantity is invalid."
            )

        if row["kind"] in {"damaged", "expired", "lost"} and quantity >= 0:
            raise InventoryValidationError(
                "Outgoing stock quantity is invalid."
            )

        return {
            "id": row["id"],
            "merchant": row["merchant"],
            "source": row["source"],
            "product": row["product"],
            "product_name": row["product_name"],
            "unit": row["unit"],
            "location_id": row["location_id"],
            "location_name": row["location_name"],
            "kind": row["kind"],
            "quantity": quantity,
            "status": row["status"],
            "cost_total": (
                int(row["cost_total"])
                if row["cost_total"] is not None
                else None
            ),
            "supplier": row["supplier"],
            "supplier_name": row["supplier_name"],
            "created": str(row["created"]),
        }
