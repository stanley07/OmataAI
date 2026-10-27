"""Merchant-scoped PostgreSQL customer operations for isolated testing.

The caller owns the PostgreSQL connection and transaction.
No implicit database connection, commit, or SQLite fallback.
"""

import re
import unicodedata

from psycopg import errors


class CustomerValidationError(ValueError):
    pass


def _actor(actor):
    if not isinstance(actor, dict):
        raise PermissionError("Authentication required")

    if (
        not actor.get("id")
        or not actor.get("merchant")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Valid authenticated actor required")

    if actor.get("role") not in (
        "owner", "admin", "manager", "cashier", "sales"
    ):
        raise PermissionError("Customer access not permitted")

    return actor["merchant"]


def list_customers(db, *, actor):
    """List customers belonging exclusively to the authenticated merchant."""
    merchant = _actor(actor)

    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, name
            FROM customers
            WHERE merchant=%s
            ORDER BY name, id
            """,
            (merchant,)
        )
        return [dict(row) for row in cur.fetchall()]


def create_customer(db, *, actor, customer_id, name):
    """Register one customer within the authenticated merchant."""
    merchant = _actor(actor)

    if actor.get("role") not in ("owner", "admin", "manager"):
        raise PermissionError(
            "Customer registration requires management authorization"
        )

    customer_id = str(customer_id or "").strip()
    name = " ".join(str(name or "").split())

    if (
        not customer_id
        or not name
        or len(customer_id) > 200
        or len(name) > 200
    ):
        raise CustomerValidationError(
            "Customer ID and name are required, maximum 200 characters."
        )

    from app import WALK_IN_CUSTOMER_ID, WALK_IN_CUSTOMER_NAME

    if (
        customer_id == WALK_IN_CUSTOMER_ID
        or name.casefold() == WALK_IN_CUSTOMER_NAME.casefold()
    ):
        raise CustomerValidationError(
            "Walk-in / Cash Customer is reserved."
        )

    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM shops WHERE id=%s",
            (merchant,)
        )

        if cur.fetchone() is None:
            raise CustomerValidationError("Merchant does not exist")

        cur.execute(
            """
            SELECT id, name
            FROM customers
            WHERE merchant=%s
              AND (id=%s OR lower(name)=lower(%s))
            """,
            (merchant, customer_id, name)
        )

        if cur.fetchone() is not None:
            raise CustomerValidationError(
                "Customer ID or name already exists in this business."
            )

        try:
            cur.execute(
                """
                INSERT INTO customers (merchant, id, name)
                VALUES (%s, %s, %s)
                RETURNING id, name
                """,
                (merchant, customer_id, name)
            )
        except errors.UniqueViolation:
            raise CustomerValidationError(
                "Customer already exists in this business."
            ) from None

        return dict(cur.fetchone())


def suggest_customer_id(name):
    """Generate a candidate identifier; registration must still check uniqueness."""
    normalized = unicodedata.normalize(
        "NFKD", str(name or "")
    ).encode("ascii", "ignore").decode("ascii").lower()

    value = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return (value or "customer")[:160]


# OMATA_STEP_172H_CUSTOMER_LOOKUP
def resolve_merchant_customer(db, *, actor, query):
    """Resolve a customer only within the authenticated merchant."""
    if not isinstance(query, str):
        raise CustomerValidationError("Customer query must be text")

    if len(query) > 200:
        raise CustomerValidationError("Customer query too long")

    from omata_pg_customer_resolution import resolve_customer

    # Existing list_customers enforces actor and merchant isolation.
    customers = list_customers(db, actor=actor)

    return resolve_customer(query, customers)
