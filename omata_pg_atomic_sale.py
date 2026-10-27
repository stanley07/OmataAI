
"""Isolated PostgreSQL atomic-sale contract.

This module performs validation only.
It does not connect to a database, create drafts, or confirm sales.
"""

from decimal import Decimal, InvalidOperation

class AtomicSaleValidationError(ValueError):
    pass


def _money_to_kobo(value):
    if isinstance(value, bool):
        raise AtomicSaleValidationError("Invalid money amount")

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise AtomicSaleValidationError("Invalid money amount") from None

    if not amount.is_finite():
        raise AtomicSaleValidationError("Invalid money amount")

    kobo = amount * 100

    if kobo != kobo.to_integral_value():
        raise AtomicSaleValidationError("Money precision exceeds kobo")

    if kobo < 0 or kobo > 10**15:
        raise AtomicSaleValidationError("Money amount outside limits")

    return int(kobo)


def validate_atomic_sale(payload, actor):
    """Validate a proposed sale without performing any database operation."""

    if not isinstance(payload, dict):
        raise AtomicSaleValidationError("Expected a sale request")

    if not isinstance(actor, dict):
        raise PermissionError("Authentication required")

    if (
        not actor.get("id")
        or not actor.get("merchant")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Valid authenticated actor required")

    if actor.get("role") not in (
        "owner", "admin", "branch_manager", "cashier"
    ):
        raise PermissionError("Actor cannot submit a sale")

    required = (
        "merchant", "source", "customer",
        "product", "location_id", "description"
    )

    values = {}

    for key in required:
        value = payload.get(key)

        if not isinstance(value, str):
            raise AtomicSaleValidationError(
                "Invalid or missing " + key
            )

        value = value.strip()

        if not value or len(value) > 2000:
            raise AtomicSaleValidationError(
                "Invalid or missing " + key
            )

        values[key] = value

    if values["merchant"] != actor["merchant"]:
        raise PermissionError("Cross-merchant sale refused")

    if values["customer"] == "walk-in-cash":
        raise AtomicSaleValidationError(
            "Registered customer required for staged credit sales"
        )

    if actor.get("role") not in ("owner", "admin"):
        allowed = {
            str(location.get("id"))
            for location in (actor.get("locations") or [])
            if isinstance(location, dict) and location.get("id")
        }

        if values["location_id"] not in allowed:
            raise PermissionError("Location assignment required")

    raw_quantity = payload.get("quantity")

    if isinstance(raw_quantity, bool):
        raise AtomicSaleValidationError(
            "Quantity must be a positive whole number"
        )

    try:
        quantity = int(raw_quantity)
    except (TypeError, ValueError, OverflowError):
        raise AtomicSaleValidationError(
            "Invalid sale quantity"
        ) from None

    if (
        str(raw_quantity).strip() != str(quantity)
        or quantity <= 0
        or quantity > 1_000_000_000
    ):
        raise AtomicSaleValidationError(
            "Quantity must be a positive whole number"
        )

    total = _money_to_kobo(payload.get("total"))
    paid = _money_to_kobo(payload.get("paid"))

    if total <= 0 or paid > total:
        raise AtomicSaleValidationError(
            "Invalid sale total or payment"
        )

    return {
        **values,
        "kind": "sale",
        "quantity": quantity,
        "total": total,
        "paid": paid,
        "debt": total - paid,
    }


# OMATA_STEP_175C_LINKED_SALE_DRAFT
def create_atomic_sale_draft(db, *, actor, payload):
    """Create linked sale/stock drafts in caller-owned PG transaction.

    No connection, commit, rollback or confirmation occurs here.
    This initial implementation is restricted to owner/admin roles.
    """
    import uuid

    sale = validate_atomic_sale(payload, actor)

    if actor.get("role") not in ("owner", "admin"):
        raise PermissionError(
            "Atomic sale drafting currently requires owner/admin"
        )

    merchant = sale["merchant"]
    source = sale["source"]

    with db.cursor() as cur:
        # Use the finance service's merchant-wide serialization lock.
        cur.execute(
            "SELECT 1 FROM shops WHERE id=%s FOR UPDATE",
            (merchant,)
        )
        if cur.fetchone() is None:
            raise AtomicSaleValidationError("Unknown merchant")

        cur.execute(
            "SELECT 1 FROM customers WHERE merchant=%s AND id=%s",
            (merchant, sale["customer"])
        )
        if cur.fetchone() is None:
            raise AtomicSaleValidationError("Customer not registered")

        cur.execute(
            "SELECT 1 FROM products WHERE merchant=%s AND id=%s",
            (merchant, sale["product"])
        )
        if cur.fetchone() is None:
            raise AtomicSaleValidationError("Product not registered")

        cur.execute(
            "SELECT 1 FROM locations WHERE merchant=%s AND id=%s",
            (merchant, sale["location_id"])
        )
        if cur.fetchone() is None:
            raise AtomicSaleValidationError("Location not registered")

        # Prevent source reuse and partial linkage.
        cur.execute(
            "SELECT id FROM entries WHERE merchant=%s AND source=%s",
            (merchant, source)
        )
        existing_entry = cur.fetchone()

        cur.execute(
            "SELECT id FROM stock_moves WHERE merchant=%s AND source=%s",
            (merchant, source)
        )
        existing_stock = cur.fetchone()

        if existing_entry or existing_stock:
            raise AtomicSaleValidationError(
                "Sale source already exists; inspect before retrying"
            )

        entry_id = str(uuid.uuid4())
        movement_id = str(uuid.uuid4())

        cur.execute(
            """INSERT INTO entries
            (id, merchant, source, kind, customer, description,
             total, paid, status, customer_credit, location_id, created_by)
            VALUES
            (%s,%s,%s,'sale',%s,%s,%s,%s,'draft',0,%s,%s)""",
            (
                entry_id, merchant, source, sale["customer"],
                sale["description"], sale["total"], sale["paid"],
                sale["location_id"], actor["id"]
            )
        )

        cur.execute(
            """INSERT INTO stock_moves
            (id, merchant, source, product, quantity, kind,
             status, entry_id, location_id)
            VALUES
            (%s,%s,%s,%s,%s,'sale','draft',%s,%s)""",
            (
                movement_id, merchant, source, sale["product"],
                -sale["quantity"], entry_id, sale["location_id"]
            )
        )

        return {
            "entry_id": entry_id,
            "stock_move_id": movement_id,
            "merchant": merchant,
            "customer": sale["customer"],
            "product": sale["product"],
            "location_id": sale["location_id"],
            "quantity": sale["quantity"],
            "total": sale["total"],
            "paid": sale["paid"],
            "debt": sale["debt"],
            "status": "draft",
            "committed": False,
        }


# OMATA_STEP_175F_CONFIRMATION_GATE
def confirm_atomic_sale(
    db,
    *,
    actor,
    merchant,
    entry_id,
    human_confirmed=False,
    review_token=None,
    review_secret=None,
):
    """Fail-closed placeholder for atomic sale confirmation.

    This function intentionally performs no SQL or writes.
    Confirmation remains unavailable until full implementation.
    """
    if not isinstance(actor, dict):
        raise PermissionError("Authenticated actor required")

    if (
        not actor.get("id")
        or not actor.get("merchant")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Valid authenticated actor required")

    if actor.get("merchant") != merchant:
        raise PermissionError("Cross-merchant confirmation refused")

    if actor.get("role") not in ("owner", "admin"):
        raise PermissionError("Management approval required")

    if human_confirmed is not True:
        raise PermissionError("Explicit human confirmation required")

    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ValueError("Sale draft ID required")

    if not review_token or not review_secret:
        raise PermissionError("Server-issued sale review token required")

    raise NotImplementedError(
        "Atomic sale confirmation is not enabled"
    )
