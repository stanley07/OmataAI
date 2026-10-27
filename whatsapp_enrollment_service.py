"""Atomic activation of approved WhatsApp employee enrollments.

The caller must validate the Meta webhook signature and sender routing.
This module never opens the production database itself.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

import app
from whatsapp_enrollment import challenge_is_valid, hash_challenge


def activate_enrollment(db, merchant, sender, receiver, supplied_challenge):
    """Activate a verified employee binding exactly once.

    Returns the binding ID. Rejects invalid, expired, or consumed challenges.
    The supplied database connection must not have an open transaction.
    """

    if db.in_transaction:
        raise RuntimeError("Activation requires a fresh transaction.")

    if not all(
        isinstance(value, str) and value
        for value in (merchant, sender, receiver, supplied_challenge)
    ):
        raise ValueError("Complete enrollment details are required.")

    if not sender.isascii() or not sender.isdigit():
        raise ValueError("Invalid WhatsApp sender.")

    if not receiver.isascii() or not receiver.isdigit():
        raise ValueError("Invalid WhatsApp receiver.")

    now = datetime.now(timezone.utc)

    db.execute("PRAGMA foreign_keys=ON")

    if db.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("Foreign-key enforcement unavailable.")

    try:
        db.execute("BEGIN IMMEDIATE")

        challenge_row = db.execute("""
            SELECT *
            FROM whatsapp_enrollment_challenges
            WHERE merchant=?
              AND sender=?
              AND receiver=?
              AND consumed_at IS NULL
              AND cancelled_at IS NULL
        """, (merchant, sender, receiver)).fetchone()

        if challenge_row is None:
            raise PermissionError("No active enrollment request found.")

        if challenge_row["locked_at"] is not None:
            raise PermissionError("Enrollment challenge is locked.")

        if challenge_row["failed_attempts"] >= 5:
            raise PermissionError("Enrollment attempt limit reached.")

        # Expired challenges are rejected without counting a guess.
        if not challenge_is_valid(
            supplied_challenge,
            challenge_row["challenge_hash"],
            challenge_row["expires_at"],
            now,
        ):
            from whatsapp_enrollment import hash_challenge

            # Only incorrect guesses against unexpired challenges count.
            from datetime import datetime as _datetime
            expiry = _datetime.fromisoformat(challenge_row["expires_at"])

            if expiry.tzinfo is None or expiry <= now:
                raise PermissionError("Invalid or expired enrollment challenge.")

            updated = db.execute("""
                UPDATE whatsapp_enrollment_challenges
                SET failed_attempts=failed_attempts+1,
                    locked_at=CASE
                        WHEN failed_attempts+1>=5
                        THEN ?
                        ELSE locked_at
                    END
                WHERE id=?
                  AND consumed_at IS NULL
                  AND cancelled_at IS NULL
                  AND locked_at IS NULL
                  AND failed_attempts<5
            """, (now.isoformat(), challenge_row["id"])).rowcount

            if updated != 1:
                raise RuntimeError("Unable to record failed enrollment attempt.")

            # Audit the fifth failure in the same transaction.
            if challenge_row["failed_attempts"] + 1 >= 5:
                app.audit(
                    db,
                    {
                        "merchant": merchant,
                        "id": challenge_row["created_by"],
                        "role": "system",
                    },
                    "whatsapp.enrollment.locked",
                    "whatsapp_enrollment",
                    challenge_row["id"],
                    {
                        "user_id": challenge_row["user_id"],
                        "failed_attempts": 5,
                        "reason": "verification_attempt_limit",
                    },
                )

            # Commit the failed attempt and audit event together.
            db.commit()
            raise PermissionError("Invalid enrollment challenge.")

        employee = db.execute("""
            SELECT *
            FROM users
            WHERE merchant=? AND id=?
              AND active=1
              AND COALESCE(must_change_password,0)=0
        """, (merchant, challenge_row["user_id"])).fetchone()

        if employee is None:
            raise PermissionError("Employee account is not eligible.")

        approving_user = db.execute("""
            SELECT *
            FROM users
            WHERE merchant=? AND id=? AND active=1
        """, (merchant, challenge_row["created_by"])).fetchone()

        if approving_user is None:
            raise PermissionError("Approving account is no longer active.")

        if approving_user["role"] not in app.AUTH_MANAGE_ROLES:
            raise PermissionError("Approving account is no longer authorized.")

        if (
            employee["role"] == "owner"
            and approving_user["role"] != "owner"
        ):
            raise PermissionError("Only owners can approve owner enrollment.")

        # Reject competing active assignments rather than replacing them.
        conflict = db.execute("""
            SELECT 1
            FROM whatsapp_user_bindings
            WHERE active=1
              AND (
                (receiver=? AND sender=?)
                OR
                (merchant=? AND user_id=? AND receiver=?)
              )
        """, (
            receiver, sender,
            merchant, employee["id"], receiver,
        )).fetchone()

        if conflict:
            raise PermissionError("An active WhatsApp binding already exists.")

        binding_id = str(uuid.uuid4())

        db.execute("""
            INSERT INTO whatsapp_user_bindings
            (id,merchant,user_id,sender,receiver,verified_at,active)
            VALUES (?,?,?,?,?,CURRENT_TIMESTAMP,1)
        """, (
            binding_id,
            merchant,
            employee["id"],
            sender,
            receiver,
        ))

        updated = db.execute("""
            UPDATE whatsapp_enrollment_challenges
            SET consumed_at=CURRENT_TIMESTAMP
            WHERE id=? AND consumed_at IS NULL
        """, (challenge_row["id"],)).rowcount

        if updated != 1:
            raise RuntimeError("Enrollment challenge was already consumed.")

        app.audit(
            db,
            {
                "merchant": merchant,
                "id": employee["id"],
                "role": employee["role"],
            },
            "whatsapp.enrollment.activate",
            "whatsapp_binding",
            binding_id,
            {
                "enrollment_id": challenge_row["id"],
                "user_id": employee["id"],
                "receiver": receiver,
            },
        )

        db.commit()
        return binding_id

    except Exception:
        db.rollback()
        raise
