# Omata AI PostgreSQL staging phase 1

This package contains the 15 reviewed source modules **unchanged** plus an isolated PostgreSQL staging helper `omata_pg_staging.py` and offline test `test_omata_pg_staging.py`.

**DO NOT replace the running SQLite application or start the staged receiver.** No existing app import points to `omata_pg_staging.py`. It does not automatically send WhatsApp messages, or mutate the staging database. Read-only `inventory_counts()` is the first integration test after setting `OMATA_PG_STAGING_URL` in a private session.

On the user's development machine (Python 3.14 with Psycopg 3 installed):

```powershell
cd "C:\Users\ezebo\Downloads\Voice-Ledger-Milestone-1\voice-ledger-postgres"
python -m unittest test_omata_pg_staging -v
```

To test read-only Neon connectivity **only after reviewing the code**, set `OMATA_PG_STAGING_URL` privately and call `omata_pg_staging.inventory_counts()`. Never share, print or commit URL values.

## Verified findings from source
- `app.connect()` currently performs hundreds of lines of SQLite DDL and SQLite schema introspection on every call; it cannot be swapped for Psycopg without separation into migration/connection layers.
- `BEGIN IMMEDIATE` is used in financial flows, and `INSERT OR IGNORE` in onboarding, inventory, and outbound queue; transaction locking and unique-key behaviors need PostgreSQL-specific rewrites.
- Several queries use SQLite `rowid` for ordering, including inventory and voice recovery. PostgreSQL needs deterministic explicit keys and ordered actions.
- SQLite backup/download routes need a PostgreSQL backup strategy rather than exposing a SQLite file as the PostgreSQL backup.
- WhatsApp enrollment migration and service modules also use PRAGMA / SQLite exception classes, and must be migrated.
- Recovery code requires `m9a97_historical_recovery_baseline.json`, which was intentionally excluded from shared ZIP. Do not start a receiver without a safe, verified replacement policy; do not enqueue 40 historical pending messages.
- User authentication needs `lower(username)=lower(%s)` lookup semantics consistent with the unique lower-index, rather than SQLite `COLLATE NOCASE`.

## Work remaining
1. Refactor `app.connect` without losing the SQLite rollback implementation.
2. Migrate all SQLite SQL, transaction boundaries and error handling, including enrollment and outbound worker; preserve exactly-once DB queue insertion and review-only ledger mutations.
3. Test read-only inventory and staging-only transaction behavior. For write tests, use **separate disposable rows** and explicitly opted-in isolated staging, never Meta send.
4. Rerun merchant separation, auth, stock, finance, inbox and voice regression suites.
5. Perform final fresh snapshot and cutover rehearsal with outage/rollback plan.

The helper's optional staged write functions are deliberately **not connected** to running code.
