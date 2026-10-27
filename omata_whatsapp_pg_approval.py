"""Staging-only WhatsApp merchant approval command parsing.

No database writes or financial confirmation.
"""

ALLOWED_COMMANDS = {
    "CONFIRM",
    "CORRECT",
    "CANCEL",
}


def parse_approval_command(message_text):
    if not isinstance(message_text, str):
        raise ValueError("Message must be text")

    command = message_text.strip().upper()

    if command not in ALLOWED_COMMANDS:
        return {
            "recognized": False,
            "action": None,
            "requires_human_review": True,
        }

    return {
        "recognized": True,
        "action": command.lower(),
        "requires_human_review": True,
    }


def prepare_approval_decision(
    *,
    message_text,
    authenticated_sender,
    expected_sender,
    merchant,
    entry_id,
):
    if merchant != "OMATA_ATOMIC_TEST":
        raise PermissionError("Staging merchant only")

    if not isinstance(authenticated_sender, str):
        raise PermissionError("Authenticated sender required")

    if not authenticated_sender.strip():
        raise PermissionError("Missing authenticated sender")

    if authenticated_sender != expected_sender:
        raise PermissionError("Sender identity mismatch")

    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ValueError("Exact draft reference required")

    parsed = parse_approval_command(message_text)

    if not parsed["recognized"]:
        return {
            "status": "unrecognized",
            "entry_id": entry_id,
            "confirmed": False,
        }

    action = parsed["action"]

    if action == "confirm":
        status = "confirmation_requested"
    elif action == "correct":
        status = "correction_requested"
    else:
        status = "cancellation_requested"

    return {
        "status": status,
        "action": action,
        "merchant": merchant,
        "entry_id": entry_id,
        "authenticated_sender": authenticated_sender,
        "requires_signed_review": action == "confirm",
        "confirmed": False,
        "financial_write_performed": False,
    }
