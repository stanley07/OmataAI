"""
Authenticated dashboard adapter for WhatsApp employee enrollment.

The HTTP handler must authenticate the session before calling this
module. Never construct the actor from client-supplied JSON.
"""

from whatsapp_enrollment_initiation import initiate_enrollment
from whatsapp_enrollment_cancellation import cancel_enrollment


def _require_manager(actor):
    if not isinstance(actor, dict):
        raise PermissionError("Authenticated manager required.")

    if actor.get("role") not in ("owner", "admin"):
        raise PermissionError("Owner or administrator required.")

    if not actor.get("merchant") or not actor.get("id"):
        raise PermissionError("Invalid authenticated identity.")

    if actor.get("must_change_password"):
        raise PermissionError("Password change required.")

    return actor


def initiate_from_dashboard(db, authenticated_actor, data):
    actor = _require_manager(authenticated_actor)

    if not isinstance(data, dict):
        raise ValueError("Expected JSON object.")

    allowed = {"user_id", "sender", "receiver"}

    unknown = set(data) - allowed
    if unknown:
        raise ValueError("Unexpected enrollment fields.")

    user_id = str(data.get("user_id") or "").strip()
    sender = str(data.get("sender") or "").strip()
    receiver = str(data.get("receiver") or "").strip()

    if not user_id or not sender or not receiver:
        raise ValueError(
            "user_id, sender and receiver are required."
        )

    # Secure challenge delivery is a prerequisite for initiation.
    # Do not generate a database challenge until a delivery mechanism
    # is implemented and independently verified.
    raise RuntimeError(
        "WhatsApp enrollment initiation is unavailable: "
        "secure challenge delivery is not configured."
    )


def cancel_from_dashboard(db, authenticated_actor, data):
    actor = _require_manager(authenticated_actor)

    if not isinstance(data, dict):
        raise ValueError("Expected JSON object.")

    allowed = {"enrollment_id", "reason"}

    unknown = set(data) - allowed
    if unknown:
        raise ValueError("Unexpected cancellation fields.")

    enrollment_id = str(
        data.get("enrollment_id") or ""
    ).strip()

    reason = str(data.get("reason") or "").strip()

    if not enrollment_id or not reason:
        raise ValueError(
            "enrollment_id and reason are required."
        )

    result = cancel_enrollment(
        db,
        actor,
        enrollment_id,
        reason,
    )

    return {"result": result}
