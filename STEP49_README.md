# M9A.98 Step 49 — isolated PostgreSQL finance implementation

Copy only `omata_pg_finance.py` and `test_omata_pg_finance.py` into `voice-ledger-postgres` (do not overwrite `app.py`, `.env`, or databases).

Run `python -m unittest test_omata_pg_finance -v` (requires previously installed `omata_pg_staging.py` and Psycopg 3).

Six offline fail-closed tests exercise actor/merchant, invalid amounts, inventory exclusion, and mismatched confirmations. **This is not yet integration tested against Neon**. No function is invoked by the app or WhatsApp; there are no outgoing messages. Both public financial functions require caller-supplied staging DB connection; any later staging invocation must use `with staging_connection(writable=True) as db` and a disposable test merchant with explicit opt-in. NEVER point these helpers at production data; confirm only after a real human action.

Implemented paths: simple sale or repayment DRAFT; duplicate (merchant, source) checking; existing customer and location check; merchant-scoped locking and entry confirmation; audit records. Intentional exclusions: stock-linked sales, walk-in sales, customer credits, payment-method records, financial reconciliations, inbox claims, backwards-compatible app integration, and WhatsApp. Live app is still SQLite. Staging data still contains a snapshot of real merchant records; do not attempt writable staging tests until a separate disposable merchant and a rollback strategy are established.

Important: `MAIN_LOCATION_ID='__main__'` based on current app source. `total`/`paid` are INTEGER minor units (e.g. 13500000 == 135000.00 naira). The stored schema remains TEXT/BIGINT as previously installed. Transaction caller owns commit/rollback. `FOR UPDATE` on shops row serializes these helpers with one another; it **does not** serialize other direct application writers. As a result this code is not cutover ready.
