# Omata AI PostgreSQL financial reads, step 62

- Standalone, read-only, authenticated-actor-aware finance projection only.
- **Not integrated into app.py**; do not start the pilot with this module or replace `/api/state` with it.
- Caller supplies a verified actor from the existing session layer and a psycopg connection with `dict_row`.
- `finance_snapshot()` returns capped recent entries and confirmed balances for management roles.
- Branch roles see only entries from assigned locations, with customer-wide balances and summary omitted to avoid disclosure.
- Does not include customer-credit, walk-in presentation rules, inventory or payment reconciliation. Not full state parity.
- Separate from original SQLite workspace and WhatsApp receiver. Tests never connect to any database.
