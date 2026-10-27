
"""Authoritative review of linked PostgreSQL sale drafts.

No database connection or commit is created here.
The caller supplies a database connection and authenticates the actor.
"""

from omata_pg_sale_review_token import issue_sale_token


class SaleReviewError(ValueError):
    pass


def review_atomic_sale(db, *, actor, merchant, entry_id, lock_rows=False):
    if (
        not isinstance(actor, dict)
        or not actor.get("id")
        or actor.get("merchant") != merchant
        or actor.get("must_change_password")
    ):
        raise PermissionError("Authenticated merchant actor required")

    if actor.get("role") not in ("owner", "admin"):
        raise PermissionError("Management review required")

    if not isinstance(entry_id, str) or not entry_id.strip():
        raise SaleReviewError("Sale entry ID required")

    # OMATA_STEP_175P_ROW_LOCK
    if type(lock_rows) is not bool:
        raise ValueError("Invalid row-lock option")
    with db.cursor() as cur:
        cur.execute(
            """SELECT id, merchant, source, kind, customer,
                      description, total, paid, customer_credit,
                      location_id, status
               FROM entries
               WHERE merchant=%s AND id=%s""" +
            (" FOR UPDATE" if lock_rows else ""),
            (merchant, entry_id),
        )
        entry = cur.fetchone()

        if entry is None:
            raise SaleReviewError("Sale draft not found")

        if entry["kind"] != "sale" or entry["status"] != "draft":
            raise SaleReviewError("Only active sale drafts can be reviewed")

        cur.execute(
            """SELECT id, merchant, source, product, quantity,
                      location_id, kind, status, entry_id
               FROM stock_moves
               WHERE merchant=%s AND entry_id=%s""" +
            (" FOR UPDATE" if lock_rows else ""),
            (merchant, entry_id),
        )
        stock = cur.fetchone()

        if stock is None:
            raise SaleReviewError("Linked inventory draft not found")

        if (
            stock["merchant"] != merchant
            or stock["entry_id"] != entry_id
            or stock["source"] != entry["source"]
            or stock["location_id"] != entry["location_id"]
            or stock["kind"] != "sale"
            or stock["status"] != "draft"
            or int(stock["quantity"]) >= 0
            or int(entry["customer_credit"] or 0) != 0
        ):
            raise SaleReviewError("Linked sale integrity check failed")

        # OMATA_STEP_175L_FINANCIAL_INTEGRITY
        # Reject malformed money fields before issuing human approval.
        for key in ("total", "paid", "customer_credit"):
            value = entry[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise SaleReviewError("Invalid financial amount: " + key)

        if (
            entry["total"] <= 0
            or entry["paid"] < 0
            or entry["paid"] > entry["total"]
            or entry["customer_credit"] != 0
            or not isinstance(entry["customer"], str)
            or not entry["customer"].strip()
        ):
            raise SaleReviewError("Financial sale integrity check failed")

        if (
            not isinstance(stock["quantity"], int)
            or isinstance(stock["quantity"], bool)
            or stock["quantity"] >= 0
            or stock["quantity"] < -1_000_000_000
        ):
            raise SaleReviewError("Invalid sale stock quantity")

        cur.execute(
            "SELECT 1 FROM customers WHERE merchant=%s AND id=%s",
            (merchant, entry["customer"]),
        )
        if cur.fetchone() is None:
            raise SaleReviewError("Customer no longer registered")

        cur.execute(
            "SELECT 1 FROM products WHERE merchant=%s AND id=%s",
            (merchant, stock["product"]),
        )
        if cur.fetchone() is None:
            raise SaleReviewError("Product no longer registered")

        cur.execute(
            "SELECT 1 FROM locations WHERE merchant=%s AND id=%s",
            (merchant, entry["location_id"]),
        )
        if cur.fetchone() is None:
            raise SaleReviewError("Location no longer registered")

    result = dict(entry)

    result.update({
        "stock_move_id": stock["id"],
        "stock_source": stock["source"],
        "stock_product": stock["product"],
        "stock_quantity": int(stock["quantity"]),
        "stock_location_id": stock["location_id"],
        "stock_kind": stock["kind"],
        "stock_status": stock["status"],
    })

    return result


def issue_atomic_sale_review(
    db, *, actor, merchant, entry_id, review_secret
):
    reviewed = review_atomic_sale(
        db,
        actor=actor,
        merchant=merchant,
        entry_id=entry_id,
    )

    token = issue_sale_token(
        sale=reviewed,
        actor=actor,
        secret=review_secret,
    )

    return {
        "sale": reviewed,
        "review_token": token,
    }
