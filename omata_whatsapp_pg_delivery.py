"""Isolated staging WhatsApp delivery payload builder.

No SQLite access, database writes, Meta API calls, or live dispatch.
"""

import json


STAGING_MERCHANT = "OMATA_ATOMIC_TEST"


def build_review_delivery(
    *,
    merchant,
    recipient,
    phone_number_id,
    review_result,
):
    if merchant != STAGING_MERCHANT:
        raise PermissionError("Only approved staging merchant allowed")

    if not isinstance(recipient, str):
        raise ValueError("Recipient must be a string")

    recipient = recipient.strip()

    if not recipient.isdigit() or not 10 <= len(recipient) <= 15:
        raise ValueError("Invalid WhatsApp recipient")

    if not isinstance(phone_number_id, str):
        raise ValueError("Invalid phone number ID")

    if not phone_number_id.isdigit():
        raise ValueError("Invalid phone number ID")

    if not isinstance(review_result, dict):
        raise ValueError("Invalid review result")

    if review_result.get("merchant") != merchant:
        raise PermissionError("Merchant mismatch")

    if review_result.get("requires_human_confirmation") is not True:
        raise ValueError("Human approval boundary missing")

    if review_result.get("confirmed") is not False:
        raise ValueError("Already confirmed or unknown state")

    if review_result.get("sent") is not False:
        raise ValueError("Delivery state already changed")

    message = review_result.get("message")

    if not isinstance(message, str):
        raise ValueError("Review message missing")

    if not message.strip() or len(message) > 4096:
        raise ValueError("Invalid review message length")

    if not isinstance(review_result.get("review_token"), str):
        raise ValueError("Signed review token missing")

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message,
        },
    }

    return {
        "merchant": merchant,
        "phone_number_id": phone_number_id,
        "payload": payload,
        "dry_run": True,
        "sent": False,
        "requires_human_confirmation": True,
    }


def payload_json(delivery):
    return json.dumps(delivery["payload"], ensure_ascii=True)
