# Step 71 — PG finance request adapter (staging only)

This package contains ONLY `omata_pg_finance_adapter.py` and its offline tests. It does not alter `app.py`, routes, SQLite, WhatsApp, Neon, or any environment setting. The adapter converts UI naira decimals into integer kobo, guards actor/merchant/location and rejects unsupported walk-in, inventory, customer-credit cases. It calls the already-tested PG finance module **only when an integration caller explicitly invokes** `draft_with_existing_connection` or `confirm_with_existing_connection` with a connection. The caller must still own the transaction and explicit human authorization. No `/api/drafts` or `/api/confirm` routes have been switched to PG.

Run: `python -m unittest -v test_omata_pg_finance_adapter`.

Important follow-on work: audit and expand authorization, location, idempotency and verified source claim consistency before app-level writes; implement a complete PG state read model and unsupported features, or expose a separate staging test-only UI rather than blending PG writes with SQLite state. Authenticated preview remains read-only.
