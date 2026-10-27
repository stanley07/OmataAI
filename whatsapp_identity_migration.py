"""Schema for verified WhatsApp employee bindings.

This module never accesses the production database automatically.
Call migrate(connection) explicitly during a controlled migration.
"""

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS whatsapp_user_bindings (
    id TEXT PRIMARY KEY,
    merchant TEXT NOT NULL,
    user_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    receiver TEXT NOT NULL,
    verified_at TEXT,
    active INTEGER NOT NULL DEFAULT 0
        CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT,

    FOREIGN KEY (merchant, user_id)
        REFERENCES users(merchant, id),

    CHECK (length(merchant) > 0),
    CHECK (length(user_id) > 0),

    CHECK (length(sender) BETWEEN 7 AND 20),
    CHECK (sender NOT GLOB '*[^0-9]*'),

    CHECK (length(receiver) BETWEEN 1 AND 30),
    CHECK (receiver NOT GLOB '*[^0-9]*'),

    CHECK (
        active = 0 OR
        (verified_at IS NOT NULL AND revoked_at IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_active_sender
ON whatsapp_user_bindings(receiver, sender)
WHERE active = 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_active_employee
ON whatsapp_user_bindings(merchant, user_id, receiver)
WHERE active = 1;
"""


def migrate(connection):
    """Install schema on an explicitly supplied SQLite connection.

    Requires an existing users table and an unused migration connection.
    """

    if connection.in_transaction:
        raise RuntimeError(
            "Migration requires a connection outside a transaction."
        )

    connection.execute("PRAGMA foreign_keys=ON")

    enabled = connection.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0]

    if enabled != 1:
        raise RuntimeError("Foreign-key enforcement is not enabled.")

    users_exists = connection.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='users'
    """).fetchone()

    if not users_exists:
        raise RuntimeError("The users table must exist first.")

    existing = connection.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='whatsapp_user_bindings'
    """).fetchone()

    if existing:
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('whatsapp_user_bindings')"
        ).fetchall()

        mapped = {
            (row[3], row[4])
            for row in foreign_keys
            if row[2] == "users"
        }

        if not {
            ("merchant", "merchant"),
            ("user_id", "id"),
        }.issubset(mapped):
            raise RuntimeError(
                "Existing WhatsApp binding table requires "
                "a separate migration review."
            )

    connection.executescript(SCHEMA)

    problems = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if problems:
        raise RuntimeError(
            "Foreign-key validation failed."
        )
