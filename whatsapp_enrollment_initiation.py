"""Owner-authorized WhatsApp enrollment initiation.

The caller must supply an actor obtained from a trusted authenticated
application session. Do not construct an actor from untrusted input.

This module does not open production storage or send WhatsApp messages.
"""

import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

import app
from whatsapp_enrollment import (
    new_challenge,
    hash_challenge,
    challenge_expiry,
)


def initiate_enrollment(db, actor, user_id, sender, receiver):
    """Create a single pending employee enrollment.

    Returns (enrollment_id, challenge, expires_at).
    The plaintext challenge must be delivered only to the intended
    WhatsApp number through a trusted delivery mechanism.
    """

    if db.in_transaction:
        raise RuntimeError("Enrollment initiation requires a fresh transaction.")

    if not actor or actor.get("role") not in app.AUTH_MANAGE_ROLES:
        raise PermissionError("Owner or administrator authorization required.")

    merchant = str(actor.get("merchant") or "").strip()
    approver_id = str(actor.get("id") or "").strip()
    user_id = str(user_id or "").strip()

    if not merchant or not approver_id or not user_id:
        raise ValueError("Complete employee authorization details required.")

    if not isinstance(sender, str) or not (
        7 <= len(sender) <= 20
        and sender.isascii()
        and sender.isdigit()
    ):
        raise ValueError("Invalid WhatsApp sender.")

    if not isinstance(receiver, str) or not (
        1 <= len(receiver) <= 30
        and receiver.isascii()
        and receiver.isdigit()
    ):
        raise ValueError("Invalid WhatsApp business receiver.")

    db.execute("PRAGMA foreign_keys=ON")

    if db.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("Foreign-key enforcement unavailable.")

    try:
        db.execute("BEGIN IMMEDIATE")

        approver = db.execute("""
            SELECT * FROM users
            WHERE merchant=? AND id=? AND active=1
        """, (merchant, approver_id)).fetchone()

        if (
            approver is None
            or approver["role"] not in app.AUTH_MANAGE_ROLES
            or approver["role"] != actor["role"]
        ):
            raise PermissionError("Approving account is not authorized.")

        employee = db.execute("""
            SELECT * FROM users
            WHERE merchant=? AND id=? AND active=1
        """, (merchant, user_id)).fetchone()

        if employee is None:
            raise ValueError("Employee not found or inactive.")

        if employee["role"] == "owner" and approver["role"] != "owner":
            raise PermissionError("Only owners can enroll another owner.")

        if employee["must_change_password"]:
            raise PermissionError(
                "Employee must complete initial account setup."
            )

        conflict = db.execute("""
            SELECT 1 FROM whatsapp_user_bindings
            WHERE active=1
              AND (
                (receiver=? AND sender=?)
                OR (merchant=? AND user_id=? AND receiver=?)
              )
        """, (receiver, sender, merchant, user_id, receiver)).fetchone()

        if conflict:
            raise PermissionError("A conflicting active binding exists.")

        now = datetime.now(timezone.utc)
        window_start = (
            now - timedelta(hours=1)
        ).strftime("%Y-%m-%d %H:%M:%S")

        recent_requests = db.execute("""
            SELECT COUNT(*)
            FROM whatsapp_enrollment_challenges
            WHERE (
                (merchant=? AND user_id=?)
                OR (receiver=? AND sender=?)
            )
              AND datetime(created_at) >= datetime(?)
        """, (
            merchant, user_id,
            receiver, sender,
            window_start,
        )).fetchone()[0]

        if recent_requests >= 3:
            raise PermissionError(
                "Enrollment rate limit reached. Try again later."
            )

        # Never modify another merchant's pending enrollment.
        foreign_pending = db.execute("""
            SELECT 1 FROM whatsapp_enrollment_challenges
            WHERE receiver=? AND sender=?
              AND merchant<>?
              AND consumed_at IS NULL
              AND cancelled_at IS NULL
        """, (receiver, sender, merchant)).fetchone()

        if foreign_pending:
            raise PermissionError(
                "WhatsApp number has an enrollment in another merchant."
            )

        # Only this merchant's terminal requests can be replaced.
        terminal_requests = db.execute("""
            SELECT id,merchant,user_id,expires_at,locked_at
            FROM whatsapp_enrollment_challenges
            WHERE merchant=? AND receiver=? AND sender=?
              AND consumed_at IS NULL
              AND cancelled_at IS NULL
              AND (expires_at<=? OR locked_at IS NOT NULL)
        """, (merchant, receiver, sender, now.isoformat())).fetchall()

        for previous in terminal_requests:
            updated = db.execute("""
                UPDATE whatsapp_enrollment_challenges
                SET cancelled_at=?
                WHERE id=? AND consumed_at IS NULL
                  AND cancelled_at IS NULL
            """, (now.isoformat(), previous["id"])).rowcount

            if updated != 1:
                raise RuntimeError("Enrollment replacement conflict.")

            app.audit(
                db,
                {
                    "merchant": previous["merchant"],
                    "id": approver_id,
                    "role": approver["role"],
                },
                "whatsapp.enrollment.expire"
                if previous["expires_at"] <= now.isoformat()
                else "whatsapp.enrollment.replace_locked",
                "whatsapp_enrollment",
                previous["id"],
                {
                    "user_id": previous["user_id"],
                    "replacement_requested_by": approver_id,
                    "cancelled_at": now.isoformat(),
                },
            )

        existing = db.execute("""
            SELECT 1 FROM whatsapp_enrollment_challenges
            WHERE receiver=? AND sender=?
              AND consumed_at IS NULL
              AND cancelled_at IS NULL
        """, (receiver, sender)).fetchone()

        if existing:
            raise PermissionError(
                "A pending enrollment already exists for this number."
            )

        challenge = new_challenge()
        expires_at = challenge_expiry(datetime.now(timezone.utc))
        enrollment_id = str(uuid.uuid4())

        db.execute("""
            INSERT INTO whatsapp_enrollment_challenges
            (id,merchant,user_id,sender,receiver,
             challenge_hash,expires_at,created_by)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            enrollment_id,
            merchant,
            user_id,
            sender,
            receiver,
            hash_challenge(challenge),
            expires_at,
            approver_id,
        ))

        app.audit(
            db,
            {
                "merchant": merchant,
                "id": approver_id,
                "role": approver["role"],
            },
            "whatsapp.enrollment.initiate",
            "whatsapp_enrollment",
            enrollment_id,
            {
                "user_id": user_id,
                "receiver": receiver,
                "expires_at": expires_at,
            },
        )

        db.commit()

        return enrollment_id, challenge, expires_at

    except Exception:
        db.rollback()
        raise
