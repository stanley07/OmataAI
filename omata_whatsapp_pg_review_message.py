"""Staging-only merchant review message preparation.

Uses the existing PostgreSQL sale review and signed-token engine.
No WhatsApp delivery or financial confirmation.
"""

from decimal import Decimal
from omata_pg_sale_review import issue_atomic_sale_review


def _naira(kobo):
    amount = Decimal(str(kobo)) / Decimal("100")
    return f"NGN {amount:,.2f}"


def prepare_review_message(
    db,
    *,
    actor,
    merchant,
    entry_id,
    review_secret,
):
    if merchant != "OMATA_ATOMIC_TEST":
        raise PermissionError("Staging merchant only")

    if not isinstance(actor, dict) or actor.get("merchant") != merchant:
        raise PermissionError("Unauthorized merchant")

    result = issue_atomic_sale_review(
        db,
        actor=actor,
        merchant=merchant,
        entry_id=entry_id,
        review_secret=review_secret,
    )

    if not isinstance(result, dict):
        raise ValueError("Invalid sale review result")

    sale = result.get("sale") or result.get("review")
    token = result.get("token") or result.get("review_token")

    if not isinstance(sale, dict) or not isinstance(token, str):
        raise ValueError("Unexpected review response structure")

    if sale.get("status") != "draft" or sale.get("stock_status") != "draft":
        raise ValueError("Sale no longer awaiting approval")

    total = Decimal(str(sale["total"]))
    paid = Decimal(str(sale["paid"]))
    debt = total - paid

    message = (
        "OMATA AI ? REVIEW SALE\n"
        f"Customer: {sale['customer']}\n"
        f"Product: {sale['stock_product']}\n"
        f"Quantity: {abs(int(sale['stock_quantity']))}\n"
        f"Total: {_naira(total)}\n"
        f"Paid: {_naira(paid)}\n"
        f"Debt: {_naira(debt)}\n"
        "Status: Awaiting human approval\n"
        "Review carefully before confirming."
    )

    return {
        "entry_id": entry_id,
        "merchant": merchant,
        "message": message,
        "review_token": token,
        "requires_human_confirmation": True,
        "confirmed": False,
        "sent": False,
    }
