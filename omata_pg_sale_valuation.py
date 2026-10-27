
"""Pure inventory valuation helper for isolated PostgreSQL testing.

No database connection, queries, commits or financial posting.
"""

from decimal import Decimal, ROUND_HALF_UP


class SaleValuationError(ValueError):
    pass


def calculate_outgoing_cost(history, current_quantity, sale_quantity):
    """Calculate outgoing stock cost using existing weighted-average rules.

    history: confirmed movements containing quantity and cost_total.
    Returns an integer cost in kobo.
    """

    if (
        isinstance(current_quantity, bool)
        or isinstance(sale_quantity, bool)
        or not isinstance(current_quantity, int)
        or not isinstance(sale_quantity, int)
    ):
        raise SaleValuationError("Quantities must be integers")

    if sale_quantity <= 0 or current_quantity < sale_quantity:
        raise SaleValuationError("Insufficient stock")

    quantity_total = 0
    value_total = 0

    for movement in history:
        try:
            quantity = movement["quantity"]
            cost = movement["cost_total"]
        except (KeyError, TypeError):
            raise SaleValuationError("Incomplete stock history") from None

        if (
            isinstance(quantity, bool)
            or isinstance(cost, bool)
            or not isinstance(quantity, int)
            or not isinstance(cost, int)
            or quantity == 0
        ):
            raise SaleValuationError("Invalid stock history")

        if quantity > 0:
            if cost <= 0:
                raise SaleValuationError("Invalid incoming valuation")

            quantity_total += quantity
            value_total += cost

        else:
            if cost < 0:
                raise SaleValuationError("Invalid outgoing valuation")

            quantity_total += quantity
            value_total -= cost

        if quantity_total < 0 or value_total < 0:
            raise SaleValuationError("Inconsistent inventory history")

    if quantity_total != current_quantity:
        raise SaleValuationError("Stock balance mismatch")

    if quantity_total <= 0 or value_total <= 0:
        raise SaleValuationError("Insufficient valued stock")

    outgoing_cost = int(
        (
            Decimal(value_total)
            * Decimal(sale_quantity)
            / Decimal(quantity_total)
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )

    if outgoing_cost > value_total:
        raise SaleValuationError("Cost exceeds inventory value")

    return outgoing_cost
