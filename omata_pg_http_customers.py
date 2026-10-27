"""Isolated Neon customer HTTP adapter.

No SQLite fallback. Caller must provide an authenticated actor.
"""

import os
from urllib.parse import urlsplit

from psycopg import connect
from psycopg.rows import dict_row

from omata_pg_customers import list_customers, create_customer, resolve_merchant_customer

TEST_HOST = "ep-dawn-night-b9t7gdwg.c-7.eu-central-1.aws.neon.tech"

READ_ROLES = {"owner", "admin", "manager", "cashier", "sales"}
WRITE_ROLES = {"owner", "admin", "manager"}


def _guard(actor, *, writable=False):
    if not isinstance(actor, dict):
        raise PermissionError("Authentication required")

    if (
        not actor.get("id")
        or not actor.get("merchant")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Authenticated customer actor required")

    roles = WRITE_ROLES if writable else READ_ROLES

    if actor.get("role") not in roles:
        raise PermissionError("Customer operation not permitted")

    if os.environ.get("OMATA_PG_FINANCE_PREVIEW") != "enabled":
        raise PermissionError("PostgreSQL testing workspace disabled")

    if writable:
        if os.environ.get("OMATA_PG_FINANCE_WRITE_TEST") != "enabled":
            raise PermissionError("Testing writes disabled")

        if os.environ.get("OMATA_PG_ALLOW_STAGING_WRITES") != "I_UNDERSTAND":
            raise PermissionError("Staging write authorization missing")

    url = os.environ.get("OMATA_PG_STAGING_URL", "").strip()
    parsed = urlsplit(url)

    if (
        parsed.scheme not in ("postgres", "postgresql")
        or parsed.hostname != TEST_HOST
        or parsed.path != "/neondb"
        or not parsed.username
        or not parsed.password
    ):
        raise RuntimeError("Incorrect Neon testing endpoint")

    return url


def execute_customer_action(action, payload, actor):
    if action not in ("list", "create", "resolve"):
        raise ValueError("Unsupported customer action")

    if not isinstance(payload, dict):
        raise ValueError("Expected request object")

    writable = action == "create"
    url = _guard(actor, writable=writable)

    if writable:
        merchant = str(payload.get("merchant") or "").strip()

        if merchant != actor["merchant"]:
            raise PermissionError("Cross-merchant customer creation refused")

    db = connect(
        url,
        sslmode="require",
        connect_timeout=15,
        row_factory=dict_row,
        autocommit=False,
    )

    try:
        if not writable:
            with db.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")

            # OMATA_STEP_172K_RESOLVE_ACTION
            if action == "resolve":
                query = payload.get("query")
                if not isinstance(query, str) or len(query) > 200:
                    raise ValueError("Invalid customer search query")

                result = resolve_merchant_customer(
                    db, actor=actor, query=query
                )

                return {
                    "resolution": result,
                    "merchant": actor["merchant"],
                    "database": "neon-finance-testing",
                }

            customers = list_customers(db, actor=actor)

            return {
                "customers": customers,
                "merchant": actor["merchant"],
                "database": "neon-finance-testing",
            }

        customer = create_customer(
            db,
            actor=actor,
            customer_id=payload.get("id"),
            name=payload.get("name"),
        )

        db.commit()

        return {
            "customer": customer,
            "merchant": actor["merchant"],
            "database": "neon-finance-testing",
        }

    except BaseException:
        db.rollback()
        raise

    finally:
        db.close()
