"""Resolve verified WhatsApp identities to current employee accounts.

No database migrations or financial mutations are performed here.
"""

import sqlite3


def resolve_whatsapp_actor(connection, merchant, sender, receiver):
    """Return the current authorized employee or None.

    The caller must already have validated the Meta webhook signature.
    """

    if not all(
        isinstance(value, str) and value.strip()
        for value in (merchant, sender, receiver)
    ):
        return None

    if not sender.isascii() or not sender.isdigit():
        return None

    if not receiver.isascii() or not receiver.isdigit():
        return None

    row = connection.execute(
        """
        SELECT u.*
        FROM whatsapp_user_bindings b
        JOIN users u
          ON u.merchant = b.merchant
         AND u.id = b.user_id
        WHERE b.merchant = ?
          AND b.sender = ?
          AND b.receiver = ?
          AND b.active = 1
          AND b.verified_at IS NOT NULL
          AND b.revoked_at IS NULL
          AND u.active = 1
          AND COALESCE(u.must_change_password, 0) = 0
        """,
        (merchant, sender, receiver),
    ).fetchone()

    if row is None:
        return None

    import app

    actor = app._public_user(connection, row)

    if not actor or not actor.get("active"):
        return None

    if actor.get("must_change_password"):
        return None

    return actor
