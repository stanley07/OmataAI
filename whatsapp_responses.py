"""Review-only WhatsApp response formatting for Omata AI."""

from decimal import Decimal, InvalidOperation

WELCOME = (
    "Hello! Welcome to Omata AI. I can help you record "
    "sales, manage inventory, expenses and customer balances. "
    "What would you like to do today?"
)

REVIEW_NOTICE = (
    "\n\nThis is a suggestion only. "
    "Review the details in your Omata AI dashboard. "
    "No business records have been changed."
)


def money(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return None
        return f"NGN {amount:,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return None


def safe_text(value, maximum=80):
    if not isinstance(value, str):
        return None

    value = " ".join(value.split())

    if not value or len(value) > maximum:
        return None

    return value


def format_response(review):
    if not isinstance(review, dict):
        return WELCOME

    suggestion = review.get("suggestion")

    if not isinstance(suggestion, dict):
        return WELCOME

    kind = suggestion.get("kind")
    warnings = suggestion.get("warnings") or []

    if isinstance(warnings, str):
        warnings = [warnings]

    if not isinstance(warnings, list):
        warnings = []

    warnings_text = " ".join(
        str(w).lower() for w in warnings
    )

    if (
        "conflict" in warnings_text
        or "correction" in warnings_text
        or "payment exceeds sale total" in warnings_text
        or "overpayment" in warnings_text
        or "exceeds" in warnings_text
    ):
        return (
            "I found conflicting or uncertain transaction details. "
            "Please clarify the instruction or review it "
            "in your dashboard."
            + REVIEW_NOTICE
        )

    if kind is None:
        return WELCOME

    product = safe_text(suggestion.get("product"))
    quantity = safe_text(suggestion.get("quantity"))
    unit = safe_text(suggestion.get("unit"))
    total = money(suggestion.get("total"))
    paid = money(suggestion.get("paid"))

    if kind == "sale":
        lines = ["I understand you want to record a sale."]

        if product:
            lines.append("Product: " + product)

        if quantity:
            lines.append(
                "Quantity: " + quantity +
                (" " + unit if unit else "")
            )

        if total:
            lines.append("Proposed total: " + total)
        else:
            lines.append("Sale total: needs clarification")

        if paid:
            lines.append("Customer payment: " + paid)
        else:
            lines.append("Customer payment: not specified")

        price_warning = review.get("price_clarification")

        if isinstance(price_warning, dict) and price_warning.get("requires_clarification"):
            from decimal import Decimal
            heard = money(Decimal(price_warning["heard_total"]) / 100)
            expected = money(Decimal(price_warning["expected_total"]) / 100)

            lines.append("")
            lines.append("AMOUNT VERIFICATION REQUIRED")
            lines.append("Amount heard: " + heard)
            if price_warning.get("branch_unspecified"):
                lines.append("Company reference price total: " + expected)
                lines.append(
                    "Please specify the sale branch. "
                    "Branch selling prices may differ."
                )
            else:
                lines.append("Configured price total: " + expected)
            lines.append(
                "Please verify or correct the amount in your dashboard. "
                "This difference may be a speech-recognition error or "
                "a legitimate price change."
            )

        return "\n".join(lines) + REVIEW_NOTICE

    if kind == "stock":
        movement = suggestion.get("stock_movement")

        labels = {
            "received": "stock received",
            "distributed": "stock distribution",
            "physical_count": "physical stock count",
            "returned_in": "stock returned",
            "returned_supplier": "return to supplier",
            "damaged": "damaged inventory",
            "expired": "expired inventory",
            "lost": "lost inventory"
        }

        label = labels.get(movement, "inventory movement")
        lines = ["I understand this as: " + label + "."]

        if product:
            lines.append("Product: " + product)

        if quantity:
            lines.append(
                "Quantity: " + quantity +
                (" " + unit if unit else "")
            )

        cost = money(suggestion.get("total_cost"))

        if cost:
            lines.append("Suggested purchase cost: " + cost)

        return "\n".join(lines) + REVIEW_NOTICE

    if kind == "repayment":
        lines = ["I understand this as a possible customer payment."]

        customer = safe_text(suggestion.get("customer_name"))

        unknown_names = {
            "someone", "somebody", "anyone", "anybody",
            "unknown", "customer", "person", "they"
        }

        if not customer or customer.casefold() in unknown_names:
            return (
                "I noticed a possible customer payment, but "
                "I cannot identify the customer reliably. "
                "Please provide the customer's name and "
                "clarify whether this is a debt repayment."
                + REVIEW_NOTICE
            )

        lines.append("Customer: " + customer)

        if paid:
            lines.append("Amount: " + paid)
        else:
            lines.append("Amount: needs clarification")

        lines.append(
            "Confirm whether this payment should be applied "
            "to an existing customer debt."
        )

        return "\n".join(lines) + REVIEW_NOTICE

    return (
        "I received your message, but I could not identify "
        "a supported transaction type."
        + REVIEW_NOTICE
    )
