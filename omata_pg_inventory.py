"""Omata AI PostgreSQL inventory foundation.

Isolated development implementation.

Caller owns:
- PostgreSQL connection
- Transaction boundaries
- Commit and rollback

This module never connects automatically, commits, or accesses SQLite.
"""

import uuid

from psycopg import errors


READ_ROLES = {
    "owner",
    "admin",
    "manager",
    "branch_manager",
    "inventory_officer",
    "cashier",
    "sales",
}

WRITE_ROLES = {
    "owner",
    "admin",
    "manager",
}


class InventoryValidationError(ValueError):
    pass


def require_actor(actor, *, writable=False):
    if not isinstance(actor, dict):
        raise PermissionError("Authentication required.")

    if (
        not actor.get("id")
        or not actor.get("merchant")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Valid authenticated actor required.")

    roles = WRITE_ROLES if writable else READ_ROLES

    if actor.get("role") not in roles:
        raise PermissionError("Inventory operation not permitted.")

    return actor["merchant"]


def list_products(db, *, actor):
    merchant = require_actor(actor)

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, unit, base_selling_price
            FROM products
            WHERE merchant = %s
            ORDER BY name, id
            """,
            (merchant,),
        )

        return [dict(row) for row in cur.fetchall()]


def register_product(
    db,
    *,
    actor,
    product_id,
    name,
    unit,
):
    merchant = require_actor(actor, writable=True)

    product_id = str(product_id or "").strip()
    name = " ".join(str(name or "").split())
    unit = " ".join(str(unit or "").split())

    if (
        not product_id
        or not name
        or not unit
        or any(len(v) > 200 for v in (product_id, name, unit))
    ):
        raise InventoryValidationError(
            "Product ID, name and unit are required (maximum 200 characters)."
        )

    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM shops WHERE id = %s",
            (merchant,),
        )

        if cur.fetchone() is None:
            raise InventoryValidationError("Business does not exist.")

        cur.execute(
            """
            SELECT 1
            FROM products
            WHERE merchant = %s AND id = %s
            """,
            (merchant, product_id),
        )

        if cur.fetchone() is not None:
            raise InventoryValidationError(
                "Product ID already exists in this business."
            )

        try:
            cur.execute(
                """
                INSERT INTO products
                    (merchant, id, name, unit)
                VALUES (%s, %s, %s, %s)
                RETURNING id, name, unit, base_selling_price
                """,
                (merchant, product_id, name, unit),
            )
        except errors.UniqueViolation:
            raise InventoryValidationError(
                "Product already exists in this business."
            ) from None

        return dict(cur.fetchone())


def branch_stock_balance(
    db,
    *,
    actor,
    product_id,
    location_id,
):
    merchant = require_actor(actor)

    product_id = str(product_id or "").strip()
    location_id = str(location_id or "").strip()

    if not product_id or not location_id:
        raise InventoryValidationError(
            "Product and location are required."
        )

    if actor["role"] in {
        "branch_manager",
        "inventory_officer",
        "cashier",
        "sales",
    }:
        allowed = {
            str(item.get("id"))
            for item in actor.get("locations", [])
            if isinstance(item, dict)
        }

        if location_id not in allowed:
            raise PermissionError(
                "Inventory location access denied."
            )

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, unit
            FROM products
            WHERE merchant = %s AND id = %s
            """,
            (merchant, product_id),
        )

        product = cur.fetchone()

        if product is None:
            raise InventoryValidationError(
                "Product does not belong to this business."
            )

        cur.execute(
            """
            SELECT id, name
            FROM locations
            WHERE merchant = %s AND id = %s
            """,
            (merchant, location_id),
        )

        location = cur.fetchone()

        if location is None:
            raise InventoryValidationError(
                "Location does not belong to this business."
            )

        cur.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS quantity
            FROM stock_moves
            WHERE merchant = %s
              AND product = %s
              AND location_id = %s
              AND status = 'confirmed'
            """,
            (merchant, product_id, location_id),
        )

        movements = int(cur.fetchone()["quantity"])

        cur.execute(
            """
            SELECT COALESCE(
                SUM(
                    CASE
                        WHEN to_location = %s THEN quantity
                        WHEN from_location = %s THEN -quantity
                        ELSE 0
                    END
                ), 0
            ) AS quantity
            FROM stock_transfers
            WHERE merchant = %s
              AND product = %s
              AND status = 'confirmed'
              AND (
                  from_location = %s
                  OR to_location = %s
              )
            """,
            (
                location_id,
                location_id,
                merchant,
                product_id,
                location_id,
                location_id,
            ),
        )

        transfers = int(cur.fetchone()["quantity"])

        balance = movements + transfers

        return {
            "merchant": merchant,
            "product": dict(product),
            "location": dict(location),
            "movement_quantity": movements,
            "transfer_quantity": transfers,
            "quantity": balance,
        }
