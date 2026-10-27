"""Isolated Neon testing inventory HTTP adapter.

No SQLite fallback. No live WhatsApp operations.
Database transactions are owned by this adapter.
"""

import os
from urllib.parse import urlsplit

from psycopg import connect
from psycopg.rows import dict_row

from omata_pg_inventory import (
    list_products,
    register_product,
    branch_stock_balance,
)
from omata_pg_stock import create_stock_draft
from omata_pg_stock_confirm import confirm_stock_draft
from omata_pg_stock_review import review_stock_draft
from omata_pg_stock_review_token import issue_token


TEST_HOST = "ep-dawn-night-b9t7gdwg.c-7.eu-central-1.aws.neon.tech"

READ_ACTIONS = {"products", "balance", "review"}
WRITE_ACTIONS = {"register", "draft", "confirm", "confirm_dry_run"}
ALLOWED_ACTIONS = READ_ACTIONS | WRITE_ACTIONS

READ_ROLES = {
    "owner", "admin", "manager",
    "branch_manager", "inventory_officer",
    "cashier", "sales",
}
WRITE_ROLES = {"owner", "admin", "manager"}


def _guard(actor, *, writable=False):
    if not isinstance(actor, dict):
        raise PermissionError("Authentication required")

    if (
        not actor.get("id")
        or not actor.get("merchant")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Valid authenticated actor required")

    roles = WRITE_ROLES if writable else READ_ROLES
    if actor.get("role") not in roles:
        raise PermissionError("Inventory access forbidden")

    if os.environ.get("OMATA_PG_FINANCE_PREVIEW") != "enabled":
        raise PermissionError("PostgreSQL testing workspace disabled")

    if writable:
        if os.environ.get("OMATA_PG_FINANCE_WRITE_TEST") != "enabled":
            raise PermissionError("PostgreSQL testing writes disabled")

        if os.environ.get("OMATA_PG_ALLOW_STAGING_WRITES") != "I_UNDERSTAND":
            raise PermissionError("Staging write opt-in missing")

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


def execute_inventory_action(action, payload, actor):
    if action not in ALLOWED_ACTIONS:
        raise ValueError("Unsupported inventory action")

    if not isinstance(payload, dict):
        raise ValueError("Expected inventory request object")

    writable = action in WRITE_ACTIONS
    url = _guard(actor, writable=writable)

    merchant = actor["merchant"]

    requested_merchant = payload.get("merchant")
    if requested_merchant is not None:
        if str(requested_merchant).strip() != merchant:
            raise PermissionError("Cross-merchant operation refused")

    if action in ("confirm", "confirm_dry_run"):
        if payload.get("human_confirmed") is not True:
            raise PermissionError("Explicit human approval required")

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

        if action == "products":
            result = {
                "products": list_products(db, actor=actor),
            }

        elif action == "register":
            result = {
                "product": register_product(
                    db,
                    actor=actor,
                    product_id=payload.get("id"),
                    name=payload.get("name"),
                    unit=payload.get("unit"),
                ),
            }

        elif action == "balance":
            result = {
                "balance": branch_stock_balance(
                    db,
                    actor=actor,
                    product_id=payload.get("product"),
                    location_id=payload.get("location_id"),
                ),
            }

        elif action == "review":
            secret = os.environ.get("OMATA_PG_STOCK_REVIEW_SECRET", "")

            if len(secret) < 32:
                raise RuntimeError(
                    "Stock review security is not configured"
                )

            reviewed = review_stock_draft(
                db,
                actor=actor,
                draft_id=payload.get("id"),
            )

            token = issue_token(
                draft=reviewed,
                actor=actor,
                secret=secret,
            )

            result = {
                "review": reviewed,
                "review_token": token,
                "expires_in_seconds": 300,
            }

        elif action == "draft":
            result = {
                "draft": create_stock_draft(
                    db,
                    actor=actor,
                    merchant=merchant,
                    source=payload.get("source"),
                    product_id=payload.get("product"),
                    location_id=payload.get("location_id"),
                    kind=payload.get("kind"),
                    quantity=payload.get("quantity"),
                    total_cost=payload.get("total_cost"),
                    supplier=payload.get("supplier"),
                ),
            }

        # OMATA_STEP_169B_CONFIRM_DRY_RUN
        elif action in ("confirm", "confirm_dry_run"):
            secret = os.environ.get("OMATA_PG_STOCK_REVIEW_SECRET", "")

            if len(secret) < 32:
                raise RuntimeError(
                    "Stock review security is not configured"
                )

            result = {
                "confirmation": confirm_stock_draft(
                    db,
                    actor=actor,
                    merchant=merchant,
                    draft_id=payload.get("id"),
                    human_confirmed=payload.get('human_confirmed'),
                    review_token=payload.get("review_token"),
                    review_secret=secret,
                ),
            }

        if action == "confirm_dry_run":
            db.rollback()
            result["dry_run"] = True
            result["committed"] = False
        elif writable:
            db.commit()
        else:
            db.rollback()

        return {
            **result,
            "merchant": merchant,
            "database": "neon-finance-testing",
        }

    except BaseException:
        db.rollback()
        raise

    finally:
        db.close()
