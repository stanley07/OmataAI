"""Persistent WhatsApp enrollment challenge schema.

This migration requires an existing users table.
It does not access production storage automatically.
"""

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS whatsapp_enrollment_challenges (
    id TEXT PRIMARY KEY,
    merchant TEXT NOT NULL,
    user_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    receiver TEXT NOT NULL,
    challenge_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    cancelled_at TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0
        CHECK (failed_attempts BETWEEN 0 AND 5),
    locked_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL,

    FOREIGN KEY (merchant, user_id)
        REFERENCES users(merchant, id),

    FOREIGN KEY (merchant, created_by)
        REFERENCES users(merchant, id),

    CHECK (length(challenge_hash) = 64),
    CHECK (length(sender) BETWEEN 7 AND 20),
    CHECK (sender NOT GLOB '*[^0-9]*'),
    CHECK (length(receiver) BETWEEN 1 AND 30),
    CHECK (receiver NOT GLOB '*[^0-9]*'),

    CHECK (
        consumed_at IS NULL OR cancelled_at IS NULL
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_pending_enrollment
ON whatsapp_enrollment_challenges(receiver, sender)
WHERE consumed_at IS NULL
  AND cancelled_at IS NULL;
"""


def migrate(connection):
    """Install the schema on an explicitly supplied connection."""

    if connection.in_transaction:
        raise RuntimeError(
            "Migration requires a connection outside a transaction."
        )

    connection.execute("PRAGMA foreign_keys=ON")

    if connection.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0] != 1:
        raise RuntimeError("Foreign-key enforcement unavailable.")

    existing = connection.execute("""
        SELECT 1
        FROM sqlite_master
        WHERE type='table'
          AND name='whatsapp_enrollment_challenges'
    """).fetchone()

    if existing:
        raise RuntimeError(
            "Existing enrollment schema requires a separate "
            "versioned migration review."
        )

    users_exists = connection.execute("""
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name='users'
    """).fetchone()

    if not users_exists:
        raise RuntimeError("The users table must exist first.")

    connection.executescript(SCHEMA)
