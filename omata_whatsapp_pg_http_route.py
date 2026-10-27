"""Staging-only WhatsApp approval HTTP service.

Not registered with app.py. No automatic dispatch or financial posting.
"""

import os

import psycopg
from psycopg.rows import dict_row

from omata_pg_atomic_sale_staging import validate_staging_url
from omata_whatsapp_pg_http_approval_handler import (
    process_authenticated_webhook,
)


def process_staging_http_approval(
    *,
    raw_body,
    signature_header,
    app_secret,
    trusted_routes,
    commit=False,
):
    if commit:
        required = (
            "OMATA_PG_WHATSAPP_HTTP_TEST",
            "OMATA_PG_SYNTHETIC_ROUTES",
            "OMATA_PG_APPROVAL_PERSIST_TEST",
        )

        if any(
            os.environ.get(name) != "enabled"
            for name in required
        ):
            raise PermissionError(
                "Staging approval persistence not authorized"
            )

    if os.environ.get("OMATA_PG_WHATSAPP_HTTP_TEST") != "enabled":
        raise PermissionError("Staging WhatsApp HTTP feature disabled")

    url = os.environ.get("OMATA_PG_STAGING_URL", "")

    if not url:
        raise RuntimeError("Staging database URL missing")

    validate_staging_url(url)

    with psycopg.connect(
        url,
        sslmode="require",
        connect_timeout=30,
        autocommit=False,
        row_factory=dict_row,
    ) as db:
        try:
            with db.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                cur.execute("SET LOCAL statement_timeout = '20s'")

                cur.execute("""
                    SELECT current_database() AS name,
                           current_setting('transaction_read_only') AS ro
                """)

                state = cur.fetchone()

                if state["name"] != "neondb" or state["ro"] != "off":
                    raise PermissionError("Approved staging database required")

            result = process_authenticated_webhook(
                db,
                raw_body=raw_body,
                signature_header=signature_header,
                app_secret=app_secret,
                trusted_routes=trusted_routes,
            )

            if commit:
                db.commit()
            else:
                db.rollback()

            return {
                **result,
                "database_committed": bool(commit),
                "financial_confirmation_executed": False,
                "whatsapp_reply_sent": False,
            }

        except Exception:
            db.rollback()
            raise
