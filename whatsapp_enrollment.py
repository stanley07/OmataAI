"""Foundation for WhatsApp employee enrollment.

This module supplies challenge generation and validation only.
It performs no database writes or network requests.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone


CHALLENGE_TTL_MINUTES = 10


def new_challenge():
    """Create a cryptographically random enrollment challenge."""
    return secrets.token_urlsafe(24)


def hash_challenge(challenge):
    """Return a fixed-length digest for secure storage."""
    if not isinstance(challenge, str) or not challenge:
        raise ValueError("Enrollment challenge is required.")
    return hashlib.sha256(
        challenge.encode("utf-8")
    ).hexdigest()


def challenge_expiry(now=None):
    """Return the UTC expiration timestamp."""
    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError("Timezone-aware datetime required.")

    return (
        now + timedelta(minutes=CHALLENGE_TTL_MINUTES)
    ).isoformat()


def challenge_is_valid(
    supplied,
    expected_hash,
    expires_at,
    now=None
):
    """Validate the challenge and its expiry.

    Single-use consumption must be enforced by the database service.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        return False

    if not isinstance(supplied, str) or not supplied:
        return False

    if not isinstance(expected_hash, str):
        return False

    try:
        expiry = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return False

    if expiry.tzinfo is None or now >= expiry:
        return False

    computed = hash_challenge(supplied)

    return hmac.compare_digest(computed, expected_hash)
