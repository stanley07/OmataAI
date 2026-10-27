"""PostgreSQL inventory draft service ? isolated testing.

Caller owns database connection and transaction.
Drafts never change confirmed stock balances.
"""

import uuid

from omata_pg_inventory import (
    require_actor,
    InventoryValidationError,
    branch_stock_balance,
)

INCOMING_KINDS = {"opening", "received"}
OUTGOING_KINDS = {"damaged", "expired", "lost"}
ALLOWED_KINDS = INCOMING_KINDS | OUTGOING_KINDS
MANAGEMENT_ROLES = {"owner", "admin", "manager"}


def create_stock_draft(
    db,
    *,
    actor,
    merchant,
    source,
    product_id,
    location_id,
    kind,
    quantity,
    total_cost=None,
    supplier=None,
):
    authenticated_merchant = require_actor(actor, writable=True)

    if merchant != authenticated_merchant:
        raise PermissionError("Cross-merchant stock operation refused")

    if actor.get("role") not in MANAGEMENT_ROLES:
        raise PermissionError("Stock draft requires management authorization")

    source = str(source or "").strip()
    product_id = str(product_id or "").strip()
    location_id = str(location_id or "").strip()
    kind = str(kind or "").strip()

    if not source or len(source) > 2000:
        raise InventoryValidationError("Valid source ID required")

    if not product_id or not location_id:
        raise InventoryValidationError("Product and location required")

    if kind not in ALLOWED_KINDS:
        raise InventoryValidationError("Unsupported stock movement type")

    if isinstance(quantity, bool):
        raise InventoryValidationError("Quantity must be a positive integer")

    try:
        q = int(quantity)
    except (ValueError, TypeError, OverflowError):
        raise InventoryValidationError("Invalid stock quantity") from None

    if str(quantity).strip() != str(q) or q <= 0 or q > 1_000_000_000:
        raise InventoryValidationError(
            "Quantity must be a positive whole number"
        )

    if kind in INCOMING_KINDS:
        if isinstance(total_cost, bool):
            raise InventoryValidationError("Invalid purchase cost")
        try:
            cost = int(total_cost)
        except (ValueError, TypeError, OverflowError):
            raise InventoryValidationError(
                "Incoming stock requires total purchase cost in kobo"
            ) from None
        if (
            str(total_cost).strip() != str(cost)
            or cost <= 0
            or cost > 10**15
        ):
            raise InventoryValidationError(
                "Incoming stock requires positive total cost in kobo"
            )
        signed_quantity = q
    else:
        if total_cost is not None:
            raise InventoryValidationError(
                "Outgoing movement cost is determined at confirmation"
            )
        cost = None
        signed_quantity = -q

    supplier_id = str(supplier or "").strip() or None

    if kind == "received" and not supplier_id:
        raise InventoryValidationError(
            "Select a registered supplier for received stock"
        )

    if kind != "received" and supplier_id:
        raise InventoryValidationError(
            "Supplier is not applicable to this movement"
        )

    # Verify merchant, product and location using existing read service.
    branch_stock_balance(
        db,
        actor=actor,
        product_id=product_id,
        location_id=location_id,
    )

    with db.cursor() as cur:
        if supplier_id:
            cur.execute(
                """SELECT 1 FROM suppliers
                   WHERE merchant=%s AND id=%s""",
                (merchant, supplier_id),
            )
            if cur.fetchone() is None:
                raise InventoryValidationError(
                    "Supplier does not belong to this business"
                )

        cur.execute(
            """SELECT id,product,quantity,kind,status,location_id,
                      cost_total,supplier
               FROM stock_moves
               WHERE merchant=%s AND source=%s""",
            (merchant, source),
        )
        existing = cur.fetchone()

        if existing:
            if (
                existing["product"] != product_id
                or int(existing["quantity"]) != signed_quantity
                or existing["kind"] != kind
                or existing["location_id"] != location_id
                or existing["cost_total"] != cost
                or existing["supplier"] != supplier_id
            ):
                raise InventoryValidationError(
                    "Source ID already used with different stock details"
                )
            return {
                "id": existing["id"],
                "status": existing["status"],
                "existing": True,
            }

        ident = str(uuid.uuid4())

        cur.execute(
            """INSERT INTO stock_moves
               (id,merchant,source,product,quantity,kind,status,
                cost_total,supplier,location_id)
               VALUES (%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s)""",
            (
                ident, merchant, source, product_id,
                signed_quantity, kind, cost,
                supplier_id, location_id,
            ),
        )

        cur.execute(
            """INSERT INTO audit_log
               (id,merchant,user_id,role,action,target_type,target_id,details)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                str(uuid.uuid4()), merchant, actor["id"],
                actor["role"], "stock.draft",
                "stock_move", ident,
                '{"status":"draft"}',
            ),
        )

        return {
            "id": ident,
            "status": "draft",
            "existing": False,
        }
