"""Read-only customer identity resolution for Omata AI.

Input records MUST already be scoped to the authenticated merchant.
This module never opens a database connection or writes transactions.
"""

import unicodedata


def _normalize(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(value.casefold().split())


def resolve_customer(query, customers):
    """Return a safe customer-resolution result.

    Exact customer-ID matches take precedence.
    Names are resolved only if exactly one record matches.
    Partial/fuzzy matching is intentionally not authoritative.
    """
    if not isinstance(query, str) or not query.strip():
        return {
            "status": "new_customer_required",
            "matches": [],
        }

    if not isinstance(customers, list):
        raise ValueError("Expected merchant-scoped customer list")

    normalized = _normalize(query)
    candidates = []

    for record in customers:
        if not isinstance(record, dict):
            raise ValueError("Invalid customer record")

        customer_id = str(record.get("id") or "").strip()
        name = str(record.get("name") or "").strip()

        if not customer_id or not name:
            raise ValueError("Incomplete customer record")

        candidates.append({
            "id": customer_id,
            "name": name,
        })

    id_matches = [
        customer for customer in candidates
        if _normalize(customer["id"]) == normalized
    ]

    matches = id_matches or [
        customer for customer in candidates
        if _normalize(customer["name"]) == normalized
    ]

    # Never silently choose between duplicate records.
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "matches": matches,
        }

    if len(matches) == 1:
        return {
            "status": "resolved",
            "customer": matches[0],
            "matches": matches,
        }

    # OMATA_STEP_172T_PARTIAL_NAME
    # Partial names suggest candidates but never resolve identity.
    if len(normalized) >= 3:
        partial = [
            customer for customer in candidates
            if _normalize(customer["name"]).startswith(normalized)
            or any(
                word.startswith(normalized)
                for word in _normalize(customer["name"]).split()
            )
        ]

        if partial:
            return {
                "status": "ambiguous",
                "matches": partial,
            }

    return {
        "status": "new_customer_required",
        "matches": [],
    }
