"""
Omata AI enrollment challenge delivery policy.

This module validates destination formatting and prevents challenge
delivery until an independently verified recipient authorization
mechanism is available.

It performs no network or database operations.
"""

import re


class EnrollmentDeliveryBlocked(PermissionError):
    pass


def normalize_whatsapp_number(value):
    if not isinstance(value, str):
        raise ValueError("WhatsApp number must be a string.")

    number = value.strip()

    if not re.fullmatch(r"[1-9][0-9]{7,14}", number):
        raise ValueError(
            "WhatsApp number must contain 8-15 international "
            "digits without spaces or a leading plus sign."
        )

    return number


def require_verified_recipient(
    *,
    merchant,
    employee_id,
    recipient,
    verification=None,
):
    """
    Deliberately fail closed.

    Caller-supplied verification flags, dictionaries or assertions
    are not trusted evidence of recipient ownership.

    Future authorization must be derived from a server-verified,
    merchant-scoped proof of control.
    """

    if not isinstance(merchant, str) or not merchant.strip():
        raise ValueError("Merchant is required.")

    if not isinstance(employee_id, str) or not employee_id.strip():
        raise ValueError("Employee ID is required.")

    normalize_whatsapp_number(recipient)

    raise EnrollmentDeliveryBlocked(
        "Enrollment recipient verification is not configured."
    )
