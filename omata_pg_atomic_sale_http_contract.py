
"""Development-only HTTP contract for inventory-linked sales.

Pure validation. No database connections, writes or route registration.
"""

ALLOWED = frozenset(("draft", "review", "confirm", "status"))


def validate_atomic_http_request(action, payload, actor):
    if action not in ALLOWED:
        raise ValueError("Unsupported atomic-sale action")

    if not isinstance(payload, dict):
        raise ValueError("Request body must be an object")

    if (
        not isinstance(actor, dict)
        or not actor.get("id")
        or not actor.get("merchant")
        or actor.get("role") not in ("owner", "admin")
        or actor.get("must_change_password")
    ):
        raise PermissionError("Management authentication required")

    merchant = payload.get("merchant")

    if (
        not isinstance(merchant, str)
        or not merchant.strip()
        or merchant != actor["merchant"]
    ):
        raise PermissionError("Authenticated business mismatch")

    if action == "draft":
        required = (
            "source",
            "customer",
            "product",
            "location_id",
            "description",
            "quantity",
            "total",
            "paid",
        )

        for field in required:
            value = payload.get(field)
            if value is None or isinstance(value, bool):
                raise ValueError("Invalid atomic-sale draft field: " + field)
            if isinstance(value, str) and not value.strip():
                raise ValueError("Missing atomic-sale draft field: " + field)

        if payload.get("kind", "sale") != "sale":
            raise ValueError("Only inventory-linked sales are supported")

        # Financial, product and stock validation remains authoritative
        # inside validate_atomic_sale and create_atomic_sale_draft.
        return dict(payload)

    entry_id = payload.get("id")

    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ValueError("Sale entry ID required")

    result = {
        "merchant": merchant,
        "id": entry_id.strip(),
    }

    if action == "confirm":
        if payload.get("human_confirmed") is not True:
            raise PermissionError("Explicit human confirmation required")

        token = payload.get("review_token")

        if not isinstance(token, str) or not token.strip():
            raise PermissionError("Signed sale review token required")

        result["human_confirmed"] = True
        result["review_token"] = token

    return result
