"""Staging WhatsApp outbox safety boundaries.

This module never sends WhatsApp messages.
"""

SYNTHETIC_RECIPIENT = "2348000000000"
SYNTHETIC_PHONE_ID = "123456789012345"
STAGING_MERCHANT = "OMATA_ATOMIC_TEST"

KEY_PREFIX = "omata-review-v1:"


def validate_staging_outbox_record(
    *,
    merchant,
    provider_key,
    sender,
    receiver,
    reply_text,
):
    if merchant != STAGING_MERCHANT:
        raise PermissionError("Non-staging merchant refused")

    if not isinstance(provider_key, str):
        raise ValueError("Invalid provider key")

    if not provider_key.startswith(KEY_PREFIX):
        raise PermissionError("Unapproved outbox key")

    if len(provider_key) != len(KEY_PREFIX) + 64:
        raise ValueError("Invalid provider key length")

    suffix = provider_key[len(KEY_PREFIX):]

    if any(c not in "0123456789abcdef" for c in suffix):
        raise ValueError("Invalid provider key digest")

    if sender != SYNTHETIC_RECIPIENT:
        raise PermissionError("Real WhatsApp recipient refused")

    if receiver != SYNTHETIC_PHONE_ID:
        raise PermissionError("Real phone number ID refused")

    if not isinstance(reply_text, str):
        raise ValueError("Invalid review message")

    if not reply_text.startswith("OMATA AI"):
        raise ValueError("Not an Omata review message")

    if "Awaiting human approval" not in reply_text:
        raise ValueError("Human review text missing")

    if not 1 <= len(reply_text) <= 4096:
        raise ValueError("Invalid message length")

    return True


def allow_meta_dispatch(*, environment):
    """Staging integration must never dispatch to Meta."""
    if environment == "staging":
        raise PermissionError(
            "Meta dispatch disabled for staging"
        )

    raise PermissionError(
        "Meta dispatch requires a separate approved live adapter"
    )
