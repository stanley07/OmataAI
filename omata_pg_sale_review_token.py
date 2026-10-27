
"""Signed review tokens for linked financial and inventory sale drafts.

Pure cryptographic module. No database connections or writes.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

MAX_AGE_SECONDS = 300

FIELDS = (
    "id",
    "merchant",
    "source",
    "kind",
    "customer",
    "description",
    "total",
    "paid",
    "customer_credit",
    "location_id",
    "status",
    "stock_move_id",
    "stock_source",
    "stock_product",
    "stock_quantity",
    "stock_location_id",
    "stock_kind",
    "stock_status",
)


def _encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value):
    return base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )


def _key(secret):
    if not isinstance(secret, str) or len(secret) < 32:
        raise RuntimeError("Strong server review secret required")
    return secret.encode("utf-8")


def fingerprint(sale):
    if not isinstance(sale, dict):
        raise ValueError("Invalid reviewed sale")

    if any(field not in sale for field in FIELDS):
        raise ValueError("Incomplete sale review data")

    canonical = {field: sale[field] for field in FIELDS}

    raw = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def issue_sale_token(*, sale, actor, secret, now=None):
    if sale["status"] != "draft" or sale["stock_status"] != "draft":
        raise ValueError("Only active linked drafts may be reviewed")

    if sale["merchant"] != actor.get("merchant"):
        raise PermissionError("Cross-merchant review refused")

    if not actor.get("id"):
        raise PermissionError("Authenticated reviewer required")

    issued = int(time.time() if now is None else now)

    payload = {
        "v": 1,
        "id": sale["id"],
        "merchant": sale["merchant"],
        "reviewer": actor["id"],
        "iat": issued,
        "nonce": secrets.token_urlsafe(16),
        "fingerprint": fingerprint(sale),
    }

    body = _encode(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))

    signature = _encode(hmac.new(
        _key(secret),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest())

    return body + "." + signature


def verify_sale_token(*, token, sale, actor, secret, now=None):
    if not isinstance(token, str) or len(token) > 4096:
        raise PermissionError("Invalid sale review token")

    parts = token.split(".")

    if len(parts) != 2:
        raise PermissionError("Invalid sale review token")

    body, signature = parts

    try:
        expected = hmac.new(
            _key(secret),
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()

        supplied = _decode(signature)

        if not hmac.compare_digest(expected, supplied):
            raise PermissionError("Invalid sale review signature")

        payload = json.loads(_decode(body))

        current = int(time.time() if now is None else now)

        if (
            payload.get("v") != 1
            or not isinstance(payload.get("iat"), int)
            or current < payload["iat"]
            or current - payload["iat"] > MAX_AGE_SECONDS
            or payload.get("id") != sale.get("id")
            or payload.get("merchant") != actor.get("merchant")
            or payload.get("reviewer") != actor.get("id")
            or payload.get("fingerprint") != fingerprint(sale)
            or sale.get("status") != "draft"
            or sale.get("stock_status") != "draft"
        ):
            raise PermissionError("Sale review expired or changed")

    except (ValueError, TypeError, KeyError, UnicodeError):
        raise PermissionError("Invalid sale review token") from None

    return True
