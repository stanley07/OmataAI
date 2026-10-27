# Omata Step 64 — isolated, opt-in PostgreSQL read preview

This changes only the isolated `voice-ledger-postgres` copy after you install it.
It does not switch `/api/state`, financial POST routes, WhatsApp, or SQLite.

Files: `app.py` (one new GET handler), `omata_pg_preview.py`, `test_omata_pg_preview.py`.

Installation: back up `app.py` in the isolated folder first, then extract ZIP into that same folder. Do not install into the running `voice-ledger` folder.

Offline test: `python -m unittest -v test_omata_pg_preview test_omata_pg_finance_read`.

Default: GET `/api/staging/finance-preview` returns 403 after valid authentication because `OMATA_PG_FINANCE_PREVIEW` is not enabled.

To enable *only in a separate local staging process*, privately configure `OMATA_PG_FINANCE_PREVIEW=enabled` and `OMATA_PG_STAGING_URL` to the unpooled Neon *omata-finance-testing* URL. The code requires the confirmed testing hostname and `neondb`. Do not expose DB credentials or enable it in the live WhatsApp process.

GET endpoint accepts the same `X-Session-Token` as `/api/state`; returns the `finance_snapshot` partial read model, not the full dashboard state. With branch roles it hides customer-wide balances. This endpoint is not called by the existing UI. Authentication still reads the local SQLite auth database; testing must establish that the authenticated merchant identifier matches that in the staging snapshot.

Security limit: branch ownership is established by a hostname check, which is not a cryptographic proof of Neon branch provenance. Recheck after resetting or swapping Neon endpoints.

Remaining tasks: proper identity/tenant mapping, read contract parity, cross-tenant/branch tests, inventory/credit/reversal/payment parity, feature-switched writes, and then UI integration.
