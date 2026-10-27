"""Staging-only Meta webhook authentication and merchant routing.

No database access, outbound delivery, or financial confirmation.
"""

import hashlib
import hmac
import json


STAGING_MERCHANT = "OMATA_ATOMIC_TEST"


def extract_verified_approval_contexts(
    *,
    raw_body,
    signature_header,
    app_secret,
    trusted_routes,
):
    if not isinstance(raw_body, bytes) or not raw_body:
        raise ValueError("Raw webhook bytes required")

    if not isinstance(app_secret, str) or not app_secret:
        raise PermissionError("Meta app secret required")

    if not isinstance(signature_header, str):
        raise PermissionError("Signature required")

    if not signature_header.startswith("sha256="):
        raise PermissionError("Invalid signature format")

    digest = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    supplied = signature_header[7:]

    if not hmac.compare_digest(digest, supplied):
        raise PermissionError("Webhook signature rejected")

    if not isinstance(trusted_routes, dict):
        raise PermissionError("Trusted routing configuration required")

    data = json.loads(raw_body)

    if not isinstance(data, dict):
        raise ValueError("Invalid webhook payload")

    if data.get("object") != "whatsapp_business_account":
        return []

    result = []

    for entry in data.get("entry", []):
        if not isinstance(entry, dict):
            continue

        for change in entry.get("changes", []):
            if not isinstance(change, dict):
                continue

            value = change.get("value", {})
            if not isinstance(value, dict):
                continue

            metadata = value.get("metadata", {})
            if not isinstance(metadata, dict):
                continue

            # Use canonical Meta phone_number_id, not display number.
            phone_id = str(
                metadata.get("phone_number_id") or ""
            ).strip()

            for message in value.get("messages", []):
                if not isinstance(message, dict):
                    continue

                if message.get("type") != "text":
                    continue

                sender = str(message.get("from") or "").strip()
                provider_id = str(message.get("id") or "").strip()

                body = message.get("text") or {}
                if not isinstance(body, dict):
                    continue

                message_text = body.get("body")

                if not (
                    sender.isdigit()
                    and phone_id.isdigit()
                    and isinstance(message_text, str)
                    and provider_id
                ):
                    continue

                # Server-controlled route identity.
                route = trusted_routes.get((phone_id, sender))

                if not isinstance(route, dict):
                    continue

                if route.get("merchant") != STAGING_MERCHANT:
                    continue

                if route.get("can_approve") is not True:
                    continue

                result.append({
                    "verified_context": {
                        "signature_verified": True,
                        "merchant_route_verified": True,
                        "merchant": STAGING_MERCHANT,
                        "sender": sender,
                        "business_phone_id": phone_id,
                    },
                    "provider_message_id": provider_id,
                    "message_text": message_text,
                })

    return result
