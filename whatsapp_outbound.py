"""Durable, single-attempt WhatsApp acknowledgement worker."""

import json
from contextlib import closing
import os
import sqlite3
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import app

REPLY = (
    "Hello! Welcome to Omata AI. I can help you record "
    "sales, manage inventory, track expenses and monitor "
    "customer balances. What would you like to do today?"
)


def initialize():
    with closing(app.connect()) as db:
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_reply_outbox (
                    provider_key TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            columns = {
                row[1] for row in db.execute(
                    "PRAGMA table_info(whatsapp_reply_outbox)"
                )
            }
            if "reply_text" not in columns:
                db.execute("""
                    ALTER TABLE whatsapp_reply_outbox
                    ADD COLUMN reply_text TEXT
                """)


def enqueue(provider_key, sender, receiver, reply_text=None):
    configured = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if receiver != configured:
        raise ValueError("Unexpected sending phone number ID")

    if reply_text is not None:
        if not isinstance(reply_text, str) or not reply_text.strip():
            raise ValueError("Reply text must be nonempty")
        if len(reply_text) > 4096:
            raise ValueError("Reply text exceeds safe length")

    with closing(app.connect()) as db:
        with db:
            db.execute("""
                INSERT OR IGNORE INTO whatsapp_reply_outbox
                    (provider_key, sender, receiver, reply_text)
                VALUES (?, ?, ?, ?)
            """, (provider_key, sender, receiver, reply_text))


def send_message(sender, receiver, reply_text=None):
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    if not token:
        raise ValueError("WHATSAPP_ACCESS_TOKEN is missing")

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": sender,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": reply_text if reply_text is not None else REPLY
        }
    }

    url = (
        "https://graph.facebook.com/v26.0/"
        + receiver + "/messages"
    )

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json"
        },
        method="POST"
    )

    with urlopen(request, timeout=20) as response:
        result = json.load(response)
        if not result.get("messages"):
            raise ValueError("Meta did not return a message ID")


def next_pending():
    with closing(app.connect()) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT provider_key, sender, receiver, reply_text
                FROM whatsapp_reply_outbox
                WHERE state = 'pending'
                ORDER BY created_at, provider_key
                LIMIT 1
            """).fetchone()

            if not row:
                return None

            key, sender, receiver, reply_text = row[0], row[1], row[2], row[3]

            db.execute("""
                UPDATE whatsapp_reply_outbox
                SET state = 'sending',
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider_key = ?
            """, (key,))

            return key, sender, receiver, reply_text


def set_state(key, state, error=None):
    with closing(app.connect()) as db:
        with db:
            db.execute("""
                UPDATE whatsapp_reply_outbox
                SET state = ?,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider_key = ?
            """, (state, error, key))


def worker():
    stop = threading.Event()

    while True:
        try:
            item = next_pending()

            if item:
                key, sender, receiver, reply_text = item

                try:
                    send_message(sender, receiver, reply_text)
                    set_state(key, "accepted")
                    print(
                        "WhatsApp reply accepted by Meta",
                        flush=True
                    )
                except HTTPError as exc:
                    set_state(
                        key, "failed",
                        "Meta HTTP " + str(exc.code)
                    )
                    print(
                        "WhatsApp reply failed: HTTP",
                        exc.code, flush=True
                    )
                except (URLError, OSError, ValueError) as exc:
                    # The outcome may be uncertain after a timeout.
                    # Do not automatically retry and risk two replies.
                    set_state(
                        key, "review",
                        type(exc).__name__
                    )
                    print(
                        "WhatsApp reply needs review:",
                        type(exc).__name__,
                        flush=True
                    )
                continue

        except (sqlite3.Error, Exception) as exc:
            print(
                "WhatsApp worker error:",
                type(exc).__name__,
                flush=True
            )

        stop.wait(1)


def start():
    if not os.environ.get("WHATSAPP_ACCESS_TOKEN"):
        raise RuntimeError("WhatsApp access token not loaded")

    initialize()
    threading.Thread(
        target=worker,
        name="omata-whatsapp-outbound",
        daemon=True
    ).start()
