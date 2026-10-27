"""Authenticated-session-only WhatsApp enrollment cancellation.

The caller must supply an actor from a verified application session.
Do not build actor dictionaries from WhatsApp messages or request bodies.
"""

import sqlite3
from datetime import datetime, timezone

import app


def cancel_enrollment(db, actor, enrollment_id, reason):
    """Cancel a pending enrollment and audit it in one transaction.

    Returns True when cancelled.
    """

    if db.in_transaction:
        raise RuntimeError("Cancellation requires a fresh transaction.")

    if not actor or actor.get("role") not in app.AUTH_MANAGE_ROLES:
        raise PermissionError("Owner or administrator required.")

    merchant = str(actor.get("merchant") or "").strip()
    actor_id = str(actor.get("id") or "").strip()
    enrollment_id = str(enrollment_id or "").strip()
    reason = str(reason or "").strip()

    if not all((merchant, actor_id, enrollment_id)):
        raise ValueError("Complete cancellation details required.")

    if not reason or len(reason) > 200:
        raise ValueError("Provide a cancellation reason (1-200 characters).")

    db.execute("PRAGMA foreign_keys=ON")

    if db.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("Foreign-key enforcement unavailable.")

    try:
        db.execute("BEGIN IMMEDIATE")

        manager = db.execute("""
            SELECT *
            FROM users
            WHERE merchant=? AND id=? AND active=1
        """, (merchant, actor_id)).fetchone()

        if (
            manager is None
            or manager["role"] not in app.AUTH_MANAGE_ROLES
            or manager["role"] != actor["role"]
            or manager["must_change_password"]
        ):
            raise PermissionError("Manager account is not authorized.")

        enrollment = db.execute("""
            SELECT *
            FROM whatsapp_enrollment_challenges
            WHERE merchant=? AND id=?
              AND consumed_at IS NULL
              AND cancelled_at IS NULL
        """, (merchant, enrollment_id)).fetchone()

        if enrollment is None:
            raise PermissionError("Pending enrollment not found.")

        employee = db.execute("""
            SELECT role
            FROM users
            WHERE merchant=? AND id=?
        """, (merchant, enrollment["user_id"])).fetchone()

        if employee is None:
            raise PermissionError("Enrollment employee not found.")

        if employee["role"] == "owner" and manager["role"] != "owner":
            raise PermissionError("Only owners can cancel owner enrollment.")

        cancelled_at = datetime.now(timezone.utc).isoformat()

        updated = db.execute("""
            UPDATE whatsapp_enrollment_challenges
            SET cancelled_at=?
            WHERE merchant=? AND id=?
              AND consumed_at IS NULL
              AND cancelled_at IS NULL
        """, (
            cancelled_at, merchant, enrollment_id
        )).rowcount

        if updated != 1:
            raise RuntimeError("Enrollment cancellation conflict.")

        # This writes within the same transaction; app.audit does not commit.
        app.audit(
            db,
            {
                "merchant": merchant,
                "id": manager["id"],
                "role": manager["role"],
            },
            "whatsapp.enrollment.cancel",
            "whatsapp_enrollment",
            enrollment_id,
            {
                "user_id": enrollment["user_id"],
                "reason": reason,
                "cancelled_at": cancelled_at,
            },
        )

        db.commit()
        return True

    except Exception:
        db.rollback()
        raise
