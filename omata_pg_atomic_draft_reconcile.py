
"""Read-only reconciliation of inventory-linked sale draft creation.

Never creates, updates, deletes or retries a financial transaction.
"""

def reconcile_atomic_draft(db, *, actor, merchant, source):
    if (
        not isinstance(actor, dict)
        or not actor.get("id")
        or actor.get("merchant") != merchant
        or actor.get("role") not in ("owner", "admin")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Management authentication required")

    if (
        not isinstance(source, str)
        or not source.strip()
        or len(source) > 2000
    ):
        raise ValueError("Valid sale source required")

    source = source.strip()

    with db.cursor() as cur:
        cur.execute(
            """SELECT id, merchant, source, kind, customer,
                      total, paid, status, location_id
               FROM entries
               WHERE merchant=%s AND source=%s""",
            (merchant, source),
        )
        entries = cur.fetchall()

        cur.execute(
            """SELECT id, merchant, source, kind, product,
                      quantity, status, location_id, entry_id,
                      cost_total
               FROM stock_moves
               WHERE merchant=%s AND source=%s""",
            (merchant, source),
        )
        moves = cur.fetchall()

    if not entries and not moves:
        return {
            "state": "not_found",
            "retry_allowed": False,
        }

    if len(entries) != 1 or len(moves) != 1:
        return {
            "state": "inconsistent",
            "reason": "Expected exactly one linked pair",
            "retry_allowed": False,
        }

    entry = entries[0]
    move = moves[0]

    valid = (
        entry["merchant"] == merchant
        and move["merchant"] == merchant
        and entry["source"] == source
        and move["source"] == source
        and entry["kind"] == "sale"
        and move["kind"] == "sale"
        and entry["status"] == "draft"
        and move["status"] == "draft"
        and str(move["entry_id"]) == str(entry["id"])
        and entry["location_id"] == move["location_id"]
        and isinstance(entry["total"], int)
        and not isinstance(entry["total"], bool)
        and entry["total"] > 0
        and isinstance(entry["paid"], int)
        and not isinstance(entry["paid"], bool)
        and 0 <= entry["paid"] <= entry["total"]
        and isinstance(move["quantity"], int)
        and not isinstance(move["quantity"], bool)
        and move["quantity"] < 0
        and move["cost_total"] is None
    )

    if not valid:
        return {
            "state": "inconsistent",
            "reason": "Linked draft content or state mismatch",
            "retry_allowed": False,
        }

    return {
        "state": "draft_committed",
        "entry_id": str(entry["id"]),
        "stock_move_id": str(move["id"]),
        "customer": entry["customer"],
        "product": move["product"],
        "location_id": entry["location_id"],
        "total": entry["total"],
        "paid": entry["paid"],
        "quantity": -move["quantity"],
        "retry_allowed": False,
    }
