
"""Isolated Neon atomic-sale connection owner.

Not connected to app.py, HTTP or WhatsApp.
Financial commits require an explicit test-only authorization.
"""

from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from omata_pg_atomic_sale_transaction import (
    confirm_atomic_sale_transaction,
    AtomicSaleCommitUncertain,
)
from omata_pg_sale_reconcile import reconcile_atomic_sale

TEST_HOST = (
    "ep-dawn-night-b9t7gdwg.c-7.eu-central-1.aws.neon.tech"
)


def validate_staging_url(url):
    if not isinstance(url, str):
        raise ValueError("PostgreSQL connection URL required")

    parsed = urlsplit(url)

    if (
        parsed.scheme not in ("postgres", "postgresql")
        or parsed.hostname != TEST_HOST
        or parsed.path != "/neondb"
        or not parsed.username
        or not parsed.password
        or parsed.port not in (None, 5432)
    ):
        raise PermissionError("Only the approved Neon test branch is allowed")

    return True


def confirm_staging_atomic_sale(
    *,
    staging_url,
    actor,
    merchant,
    entry_id,
    human_confirmed=False,
    review_token=None,
    review_secret=None,
    allow_test_commit=False,
):
    validate_staging_url(staging_url)

    if allow_test_commit is not True:
        raise PermissionError(
            "Persistent staging confirmation is disabled"
        )

    if human_confirmed is not True:
        raise PermissionError("Explicit human approval required")

    if not isinstance(actor, dict) or actor.get("merchant") != merchant:
        raise PermissionError("Authenticated merchant actor required")

    if not review_token or not review_secret:
        raise PermissionError("Signed sale review required")

    # Fresh, exclusively owned connection for this confirmation.
    with psycopg.connect(
        staging_url,
        sslmode="require",
        connect_timeout=15,
        row_factory=dict_row,
        autocommit=False,
    ) as db:
        # OMATA_STEP_175X_COMMIT_RECONCILIATION
        try:
            return confirm_atomic_sale_transaction(
                db,
                actor=actor,
                merchant=merchant,
                entry_id=entry_id,
                human_confirmed=True,
                review_token=review_token,
                review_secret=review_secret,
            )
        except AtomicSaleCommitUncertain:
            # The original connection may no longer be usable.
            # Never automatically repeat the financial write.
            pass

    # Open an entirely new connection for read-only reconciliation.
    # Do not reuse the original connection after uncertain COMMIT.
    try:
        with psycopg.connect(
            staging_url,
            sslmode="require",
            connect_timeout=15,
            row_factory=dict_row,
            autocommit=False,
        ) as check_db:
            with check_db.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")

            result = reconcile_atomic_sale(
                check_db,
                actor=actor,
                merchant=merchant,
                entry_id=entry_id,
            )

            check_db.rollback()

        return {
            "commit_uncertain": True,
            "reconciliation": result,
            "retry_performed": False,
        }

    except Exception as exc:
        raise AtomicSaleCommitUncertain(
            "Commit outcome remains unknown; manual reconciliation "
            "required before any retry"
        ) from exc
