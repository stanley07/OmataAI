
"""Development-only atomic-sale transaction owner.

Caller supplies a fresh, dedicated database connection.
Not connected to HTTP or WhatsApp.
"""

from omata_pg_atomic_confirm_engine import confirm_linked_sale


class AtomicSaleCommitUncertain(RuntimeError):
    """Commit outcome may be unknown; reconcile before retrying."""


def confirm_atomic_sale_transaction(
    db,
    *,
    actor,
    merchant,
    entry_id,
    human_confirmed=False,
    review_token=None,
    review_secret=None,
):
    if getattr(db, "autocommit", False):
        raise RuntimeError("Autocommit connections are forbidden")

    # OMATA_STEP_175U_IDLE_CONNECTION
    # Prevent commits of unrelated pending transactions.
    from psycopg.pq import TransactionStatus

    if getattr(db, "closed", True):
        raise RuntimeError("A live dedicated connection is required")

    pgconn = getattr(db, "pgconn", None)

    if pgconn is None:
        raise RuntimeError("PostgreSQL connection required")

    if pgconn.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError(
            "Connection must be idle before atomic confirmation"
        )

    if not isinstance(actor, dict):
        raise PermissionError("Authenticated actor required")

    if actor.get("merchant") != merchant:
        raise PermissionError("Cross-merchant confirmation refused")

    if human_confirmed is not True:
        raise PermissionError("Explicit human approval required")

    try:
        result = confirm_linked_sale(
            db,
            actor=actor,
            merchant=merchant,
            entry_id=entry_id,
            human_confirmed=True,
            review_token=review_token,
            review_secret=review_secret,
        )
    except BaseException:
        db.rollback()
        raise

    try:
        db.commit()
    except BaseException as exc:
        try:
            db.rollback()
        except Exception:
            pass

        raise AtomicSaleCommitUncertain(
            "Commit outcome uncertain; reconcile PostgreSQL "
            "before attempting another confirmation"
        ) from exc

    return {
        **result,
        "committed": True,
    }
