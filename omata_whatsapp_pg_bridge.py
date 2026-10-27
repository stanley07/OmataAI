"""Staging-only WhatsApp parser to PostgreSQL sale adapter.

Pure proposal mapping. No database access or financial writes.
"""

from decimal import Decimal, InvalidOperation
from omata_pg_atomic_sale import validate_atomic_sale


class BridgeValidationError(ValueError):
    pass


def _positive_integer(value, field):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BridgeValidationError(f"Invalid {field}") from None

    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise BridgeValidationError(f"Invalid {field}")

    return int(number)


def _money(value, field):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BridgeValidationError(f"Invalid {field}") from None

    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise BridgeValidationError(f"Invalid {field}")

    return format(amount, "f")


def build_sale_proposal(
    *,
    parsed,
    actor,
    merchant,
    source,
    customer_id,
    product_id,
    location_id,
    description,
):
    """Construct a validated proposal. Never writes to a ledger."""

    if not isinstance(parsed, dict) or parsed.get("kind") != "sale":
        raise BridgeValidationError("A verified sale interpretation is required")

    if not isinstance(actor, dict) or actor.get("merchant") != merchant:
        raise BridgeValidationError("Merchant authorization mismatch")

    for name, value in (
        ("merchant", merchant),
        ("source", source),
        ("customer", customer_id),
        ("product", product_id),
        ("location", location_id),
        ("description", description),
    ):
        if not isinstance(value, str) or not value.strip():
            raise BridgeValidationError(f"Missing {name}")

    if not source.startswith("wa-staging:"):
        raise BridgeValidationError("Only staging message sources are accepted")

    quantity = _positive_integer(parsed.get("quantity"), "quantity")
    total = _money(parsed.get("total"), "total")
    paid = _money(parsed.get("paid"), "paid")

    if Decimal(paid) > Decimal(total):
        raise BridgeValidationError("Payment exceeds total")

    payload = {
        "merchant": merchant,
        "source": source,
        "customer": customer_id,
        "product": product_id,
        "location_id": location_id,
        "description": description,
        "quantity": quantity,
        "total": total,
        "paid": paid,
    }

    validated = validate_atomic_sale(payload, actor)

    return {
        "payload": payload,
        "validated": validated,
        "requires_human_confirmation": True,
        "committed": False,
        "parser_warnings": list(parsed.get("warnings") or []),
    }


# OMATA_STEP_184P_SCOPED_IDENTITY
def build_verified_sale_proposal(
    db,
    *,
    parsed,
    actor,
    merchant,
    source,
    customer_id,
    product_id,
    location_id,
    description,
):
    """Verify merchant-owned identities before building a proposal.

    The caller must provide an authenticated actor and a PostgreSQL
    connection. This function performs SELECT queries only.
    """

    if not isinstance(actor, dict):
        raise BridgeValidationError("Authenticated actor required")

    if actor.get("merchant") != merchant:
        raise BridgeValidationError("Merchant authorization mismatch")

    if merchant != "OMATA_ATOMIC_TEST":
        raise BridgeValidationError("Staging merchant only")

    checks = (
        ("customers", customer_id),
        ("products", product_id),
        ("locations", location_id),
    )

    if not isinstance(merchant, str) or not merchant:
        raise BridgeValidationError("Invalid merchant")

    for table, ident in checks:
        if not isinstance(ident, str) or not ident.strip():
            raise BridgeValidationError("Missing verified identity")

        # Fixed table names; never derived from user input.
        with db.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM public.{table}
                WHERE merchant=%s AND id=%s
                """,
                (merchant, ident),
            )

            result = cur.fetchone()
            count = result[0] if not isinstance(result, dict) else next(iter(result.values()))

            if count != 1:
                raise BridgeValidationError(
                    f"{table} identity not available to merchant"
                )

    return build_sale_proposal(
        parsed=parsed,
        actor=actor,
        merchant=merchant,
        source=source,
        customer_id=customer_id,
        product_id=product_id,
        location_id=location_id,
        description=description,
    )
