"""Isolated HTTP-to-approval adapter for Omata staging.

This module does not connect to PostgreSQL or send Meta messages.
Only server-side code may supply the app secret and trusted routes.
"""

from omata_whatsapp_pg_trusted_webhook import (
    extract_verified_approval_contexts,
)


def authenticate_inbound_approvals(
    *,
    raw_body,
    signature_header,
    app_secret,
    trusted_routes,
):
    """Authenticate a Meta webhook and select permitted review commands."""

    if not isinstance(raw_body, bytes):
        raise ValueError("Expected raw HTTP request bytes")

    messages = extract_verified_approval_contexts(
        raw_body=raw_body,
        signature_header=signature_header,
        app_secret=app_secret,
        trusted_routes=trusted_routes,
    )

    result = []

    for message in messages:
        body = message.get("message_text", "")

        if not isinstance(body, str):
            continue

        parts = body.strip().upper().split()

        if len(parts) != 2:
            continue

        command, session_id = parts

        if command not in ("CONFIRM", "CORRECT", "CANCEL"):
            continue

        if not session_id.startswith("RV"):
            continue

        if not (6 <= len(session_id) <= 24):
            continue

        result.append({
            "authenticated_message": message,
            "session_id": session_id,
            "command": command,
        })

    return result
