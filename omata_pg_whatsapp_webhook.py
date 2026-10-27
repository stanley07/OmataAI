"""Draft-only WhatsApp PostgreSQL webhook processor.

No HTTP listener, outbound sender or financial mutations.
"""

import hashlib
import hmac
import json

from omata_pg_whatsapp_ingest import save_inbound_text


class WebhookValidationError(Exception):
    pass


def process_webhook(raw_body, signature, app_secret, routes, *, save=None):
    if not isinstance(raw_body, bytes) or not raw_body:
        raise WebhookValidationError("Invalid request body")

    if len(raw_body) > 1024 * 1024:
        raise WebhookValidationError("Payload too large")

    if not isinstance(app_secret, str) or not app_secret:
        raise WebhookValidationError("Missing app secret")

    if not isinstance(signature, str):
        raise WebhookValidationError("Missing signature")

    expected = "sha256=" + hmac.new(
        app_secret.encode(),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise WebhookValidationError("Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookValidationError("Invalid JSON") from exc

    if not isinstance(payload, dict):
        raise WebhookValidationError("Invalid payload")

    if payload.get("object") != "whatsapp_business_account":
        raise WebhookValidationError("Unsupported webhook object")

    if not isinstance(routes, dict):
        raise WebhookValidationError("Invalid merchant routing")

    save = save or save_inbound_text

    accepted = 0
    ignored = 0
    duplicates = 0

    entries = payload.get("entry", [])

    if not isinstance(entries, list):
        raise WebhookValidationError("Invalid entries")

    for entry in entries:
        if not isinstance(entry, dict):
            raise WebhookValidationError("Invalid entry")

        changes = entry.get("changes", [])

        if not isinstance(changes, list):
            raise WebhookValidationError("Invalid changes")

        for change in changes:
            if not isinstance(change, dict):
                raise WebhookValidationError("Invalid change")

            if change.get("field") != "messages":
                continue

            value = change.get("value", {})

            if not isinstance(value, dict):
                raise WebhookValidationError("Invalid message data")

            metadata = value.get("metadata", {})

            if not isinstance(metadata, dict):
                raise WebhookValidationError("Invalid metadata")

            receiver = metadata.get("phone_number_id")
            messages = value.get("messages", [])

            if not isinstance(messages, list):
                raise WebhookValidationError("Invalid messages")

            for message in messages:
                if not isinstance(message, dict):
                    raise WebhookValidationError("Invalid message")

                sender = message.get("from")
                merchant = routes.get((receiver, sender))

                if not merchant or message.get("type") != "text":
                    ignored += 1
                    continue

                message_id = message.get("id")
                text_data = message.get("text", {})

                if not isinstance(text_data, dict):
                    ignored += 1
                    continue

                body = text_data.get("body")

                if (
                    not isinstance(message_id, str)
                    or not message_id
                    or len(message_id) > 500
                    or not isinstance(body, str)
                    or not body.strip()
                    or len(body) > 2000
                ):
                    ignored += 1
                    continue

                result = save(
                    merchant,
                    "wa:" + receiver + ":" + message_id,
                    sender,
                    receiver,
                    body,
                )

                if result["created"]:
                    accepted += 1
                else:
                    duplicates += 1

    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "ignored": ignored,
    }
