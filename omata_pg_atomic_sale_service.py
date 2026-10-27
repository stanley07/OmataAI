
"""Development-only staging atomic-sale service.

No HTTP routes are registered here.
No database connection is opened by importing this module.
All financial writes require explicit staging authorization.
"""

import os

from omata_pg_atomic_sale_http_contract import (
    validate_atomic_http_request,
)
from omata_pg_atomic_sale_staging import (
    validate_staging_url,
    confirm_staging_atomic_sale,
)
from omata_pg_atomic_sale_transaction import (
    AtomicSaleCommitUncertain,
)


class AtomicSaleServiceDisabled(PermissionError):
    pass


class AtomicSaleDraftCommitUncertain(RuntimeError):
    """Never blindly retry draft creation after interrupted COMMIT."""


def require_staging_settings():
    """Fail closed unless isolated staging writes are authorized."""
    if os.environ.get("OMATA_PG_ATOMIC_SALE_TEST") != "enabled":
        raise AtomicSaleServiceDisabled(
            "Atomic-sale staging service disabled"
        )

    if os.environ.get("OMATA_PG_ALLOW_STAGING_WRITES") != "I_UNDERSTAND":
        raise AtomicSaleServiceDisabled(
            "Staging write authorization missing"
        )

    url = os.environ.get("OMATA_PG_STAGING_URL", "").strip()
    validate_staging_url(url)

    secret = os.environ.get("OMATA_PG_SALE_REVIEW_SECRET", "")

    if len(secret) < 32:
        raise AtomicSaleServiceDisabled(
            "Server-side sale review secret unavailable"
        )

    return url, secret


def validate_service_request(action, payload, actor):
    """Validate identity and request before opening PostgreSQL."""
    return validate_atomic_http_request(action, payload, actor)


def execute_atomic_sale(action, payload, actor):
    """Fail-closed staging dispatch; database actions not yet enabled."""
    request = validate_service_request(action, payload, actor)

    # Require valid isolated test configuration even for development
    # dispatch. This is not an API endpoint.
    require_staging_settings()

    # OMATA_STEP_178G_DRAFT_DISPATCH
    # OMATA_STEP_178L_READ_ONLY_REVIEW
    if action == "review":
        import psycopg
        from psycopg.rows import dict_row
        from omata_pg_sale_review import issue_atomic_sale_review

        url, review_secret = require_staging_settings()

        with psycopg.connect(
            url,
            sslmode="require",
            connect_timeout=15,
            row_factory=dict_row,
            autocommit=False,
        ) as review_db:
            with review_db.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '10s'")

            result = issue_atomic_sale_review(
                review_db,
                actor=actor,
                merchant=request["merchant"],
                entry_id=request["id"],
                review_secret=review_secret,
            )

            review_db.rollback()

        if not isinstance(result, dict):
            raise RuntimeError("Invalid atomic-sale review response")

        if not isinstance(result.get("sale"), dict):
            raise RuntimeError("Authoritative sale review missing")

        if not isinstance(result.get("review_token"), str):
            raise RuntimeError("Signed review token missing")

        if not result["review_token"]:
            raise RuntimeError("Empty review token")

        return {
            "sale": result["sale"],
            "review_token": result["review_token"],
            "database": "neon-atomic-sale-testing",
            "read_only": True,
        }

    # OMATA_STEP_178M_READ_ONLY_STATUS
    if action == "status":
        import psycopg
        from psycopg.rows import dict_row
        from omata_pg_sale_reconcile import reconcile_atomic_sale

        url, _secret = require_staging_settings()

        with psycopg.connect(
            url,
            sslmode="require",
            connect_timeout=15,
            row_factory=dict_row,
            autocommit=False,
        ) as status_db:
            with status_db.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '10s'")

            result = reconcile_atomic_sale(
                status_db,
                actor=actor,
                merchant=request["merchant"],
                entry_id=request["id"],
            )

            status_db.rollback()

        if not isinstance(result, dict):
            raise RuntimeError("Invalid sale reconciliation result")

        if result.get("state") not in (
            "confirmed",
            "not_committed",
            "inconsistent",
        ):
            raise RuntimeError("Unknown sale reconciliation state")

        return {
            "reconciliation": result,
            "database": "neon-atomic-sale-testing",
            "read_only": True,
        }

    # OMATA_STEP_178N_CONFIRM_DISPATCH
    if action == "confirm":
        if os.environ.get("OMATA_PG_ATOMIC_CONFIRM_TEST") != "enabled":
            raise AtomicSaleServiceDisabled(
                "Atomic-sale confirmation disabled"
            )

        from omata_pg_atomic_sale_staging import (
            confirm_staging_atomic_sale,
        )

        url, review_secret = require_staging_settings()

        return confirm_staging_atomic_sale(
            staging_url=url,
            actor=actor,
            merchant=request["merchant"],
            entry_id=request["id"],
            human_confirmed=request["human_confirmed"],
            review_token=request["review_token"],
            review_secret=review_secret,
            allow_test_commit=True,
        )

    if action != "draft":
        raise AtomicSaleServiceDisabled(
            "Atomic-sale action not enabled"
        )

    import psycopg
    from psycopg.rows import dict_row
    from omata_pg_atomic_sale import create_atomic_sale_draft

    url, _secret = require_staging_settings()

    db = psycopg.connect(
        url,
        sslmode="require",
        connect_timeout=15,
        row_factory=dict_row,
        autocommit=False,
    )

    try:
        from psycopg.pq import TransactionStatus

        if (
            db.closed
            or db.autocommit
            or db.pgconn.transaction_status != TransactionStatus.IDLE
        ):
            raise RuntimeError("Fresh PostgreSQL connection required")

        result = create_atomic_sale_draft(
            db,
            actor=actor,
            payload=request,
        )

        # OMATA_STEP_178H_COMMIT_SAFETY
        try:
            db.commit()
        except BaseException as exc:
            try:
                db.rollback()
            except Exception:
                pass
            raise AtomicSaleDraftCommitUncertain(
                "Draft COMMIT outcome uncertain; inspect the "
                "existing source before creating another draft"
            ) from exc

        return {
            **result,
            "committed": True,
            "database": "neon-atomic-sale-testing",
        }

    except AtomicSaleDraftCommitUncertain:
        # OMATA_STEP_178J_SOURCE_RECONCILIATION
        # Original connection may be unusable.
        # Never repeat draft creation.
        try:
            import psycopg
            from psycopg.rows import dict_row
            from omata_pg_atomic_draft_reconcile import (
                reconcile_atomic_draft,
            )

            with psycopg.connect(
                url,
                sslmode="require",
                connect_timeout=15,
                row_factory=dict_row,
                autocommit=False,
            ) as check_db:
                with check_db.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")

                reconciliation = reconcile_atomic_draft(
                    check_db,
                    actor=actor,
                    merchant=request["merchant"],
                    source=request["source"],
                )
                check_db.rollback()

            return {
                "commit_uncertain": True,
                "reconciliation": reconciliation,
                "retry_performed": False,
            }

        except Exception as exc:
            raise AtomicSaleDraftCommitUncertain(
                "Draft outcome remains uncertain; "
                "manual reconciliation required"
            ) from exc

    except BaseException:
        try:
            db.rollback()
        except Exception:
            pass
        raise

    finally:
        db.close()
