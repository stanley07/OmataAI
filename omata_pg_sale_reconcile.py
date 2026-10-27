
"""Read-only reconciliation after uncertain atomic-sale commits."""

class SaleReconciliationError(ValueError):
    pass


def reconcile_atomic_sale(db, *, actor, merchant, entry_id):
    if (
        not isinstance(actor, dict)
        or not actor.get("id")
        or actor.get("merchant") != merchant
        or actor.get("role") not in ("owner", "admin")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Management authentication required")

    if not isinstance(entry_id, str) or not entry_id.strip():
        raise SaleReconciliationError("Sale entry ID required")

    with db.cursor() as cur:
        cur.execute(
            """SELECT id, merchant, source, kind, customer,
                      total, paid, status, location_id
               FROM entries WHERE merchant=%s AND id=%s""",
            (merchant, entry_id),
        )
        entry = cur.fetchone()

        if entry is None:
            return {
                "state": "inconsistent",
                "reason": "Sale entry missing; investigate before retry",
            }

        cur.execute(
            """SELECT id, merchant, source, product, quantity,
                      status, cost_total, location_id, entry_id
               FROM stock_moves
               WHERE merchant=%s AND entry_id=%s""",
            (merchant, entry_id),
        )
        stock = cur.fetchone()

        if stock is None:
            return {
                "state": "inconsistent",
                "reason": "Linked stock movement missing",
            }

        if (
            entry["kind"] != "sale"
            or stock["merchant"] != merchant
            or stock["entry_id"] != entry_id
            or stock["source"] != entry["source"]
            or stock["location_id"] != entry["location_id"]
            or int(stock["quantity"]) >= 0
        ):
            return {
                "state": "inconsistent",
                "reason": "Linked sale data mismatch",
            }

        cur.execute(
            """SELECT action, target_id, COUNT(*) AS n
               FROM audit_log
               WHERE merchant=%s
                 AND (
                     (action='entry.confirm' AND target_id=%s)
                     OR
                     (action='stock.confirm' AND target_id=%s)
                 )
               GROUP BY action, target_id""",
            (merchant, entry_id, stock["id"]),
        )

        audits = {
            (row["action"], row["target_id"]): int(row["n"])
            for row in cur.fetchall()
        }

    entry_audits = audits.get(("entry.confirm", entry_id), 0)
    stock_audits = audits.get(("stock.confirm", stock["id"]), 0)

    if (
        entry["status"] == "confirmed"
        and stock["status"] == "confirmed"
        and entry_audits == 1
        and stock_audits == 1
        and stock["cost_total"] is not None
    ):
        return {
            "state": "confirmed",
            "entry_id": entry_id,
            "stock_move_id": stock["id"],
            "customer": entry["customer"],
            "total": int(entry["total"]),
            "paid": int(entry["paid"]),
            "debt": int(entry["total"]) - int(entry["paid"]),
        }

    if (
        entry["status"] == "draft"
        and stock["status"] == "draft"
        and entry_audits == 0
        and stock_audits == 0
    ):
        return {
            "state": "not_committed",
            "entry_id": entry_id,
            "stock_move_id": stock["id"],
        }

    return {
        "state": "inconsistent",
        "reason": "Confirmation state or audit trail mismatch",
    }
