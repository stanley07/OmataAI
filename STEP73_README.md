# Omata Step 73: Isolated HTTP staging financial writes

Copy `app.py`, `omata_pg_http_finance.py`, and `test_omata_pg_http_finance.py` into your ISOLATED `voice-ledger-postgres` workspace. Backup the existing `app.py` first.

- Leaves `/api/drafts`, `/api/confirm`, `/api/reverse`, `/api/state` and WhatsApp processing on SQLite.
- Adds separate staging-only POST `/api/staging/finance-drafts` and `/api/staging/finance-confirm`.
- Reads remain on the existing staging `GET /api/staging/finance-preview` endpoint.
- Disabled unless ALL `OMATA_PG_FINANCE_WRITE_TEST=enabled`, `OMATA_PG_FINANCE_PREVIEW=enabled`, `OMATA_PG_ALLOW_STAGING_WRITES=I_UNDERSTAND` are set **within isolated process**, along with a validated test branch URL. DO NOT set these yet.
- Confirm action requires `human_confirmed: true` in the request **and** a valid authenticated HTTP session. This is a test-specific extra check, not proof that a real dashboard confirmation click occurred; never expose test routes publicly.
- Only finance types supported by the adapter; inventory, walk-ins and customer-credit overpayments rejected; no PostgreSQL reversal route.
- Writes COMMIT into the TEST branch if explicitly enabled; **not rollback-only**. Keep disabled until separate end-to-end tests and disposable fixtures are ready.
- This is NOT full production migration or dashboard state replacement.

Smoke tests:
`python -m py_compile app.py omata_pg_http_finance.py`
`python -m unittest -v test_omata_pg_http_finance test_omata_pg_finance_adapter test_omata_pg_preview test_omata_pg_finance_read`

Avoid pasting credentials or customer IDs in chat.
