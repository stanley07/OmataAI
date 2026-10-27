"""Signed, time-limited stock draft review tokens.

Caller supplies the secret and authoritative reviewed draft.
No database connections, commits or SQLite fallback.

A token alone does not prove visual human review.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

MAX_AGE_SECONDS = 300


def _encode(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value):
    return base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )


def _fingerprint(draft):
    fields = (
        "id", "merchant", "source", "product",
        "location_id", "kind", "quantity", "cost_total",
        "supplier", "status",
    )
    canonical = {
        field: draft.get(field)
        for field in fields
    }
    raw = json.dumps(
        canonical, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _key(secret):
    if not isinstance(secret, str) or len(secret) < 32:
        raise RuntimeError("Strong server review secret required")
    return secret.encode("utf-8")


def issue_token(*, draft, actor, secret, now=None):
    if draft.get("status") != "draft":
        raise ValueError("Only active drafts can be reviewed")
    if draft.get("merchant") != actor.get("merchant"):
        raise PermissionError("Merchant mismatch")
    if not actor.get("id"):
        raise PermissionError("Authenticated reviewer required")

    issued = int(time.time() if now is None else now)

    payload = {
        "v": 1,
        "id": draft["id"],
        "merchant": draft["merchant"],
        "reviewer": actor["id"],
        "iat": issued,
        "nonce": secrets.token_urlsafe(16),
        "fingerprint": _fingerprint(draft),
    }

    body = _encode(json.dumps(
        payload, separators=(",", ":"), sort_keys=True
    ).encode("utf-8"))

    signature = _encode(hmac.new(
        _key(secret), body.encode("ascii"),
        hashlib.sha256
    ).digest())

    return body + "." + signature


def verify_token(*, token, draft, actor, secret, now=None):
    if not isinstance(token, str) or len(token) > 4096:
        raise PermissionError("Invalid review token")

    parts = token.split(".")
    if len(parts) != 2:
        raise PermissionError("Invalid review token")

    body, signature = parts

    try:
        supplied = _decode(signature)
        expected = hmac.new(
            _key(secret),
            body.encode("ascii"),
            hashlib.sha256
        ).digest()

        if not hmac.compare_digest(supplied, expected):
            raise PermissionError("Invalid review signature")

        payload = json.loads(_decode(body))

        current = int(time.time() if now is None else now)

        if (
            payload.get("v") != 1
            or not isinstance(payload.get("iat"), int)
            or current < payload["iat"]
            or current - payload["iat"] > MAX_AGE_SECONDS
            or payload.get("id") != draft.get("id")
            or payload.get("merchant") != actor.get("merchant")
            or payload.get("reviewer") != actor.get("id")
            or payload.get("fingerprint") != _fingerprint(draft)
            or draft.get("status") != "draft"
        ):
            raise PermissionError("Review expired or draft changed")

    except (ValueError, TypeError, KeyError, UnicodeError):
        raise PermissionError("Invalid review token") from None

    return True
