-- Omata AI PostgreSQL staging schema V2
-- Review and validate before executing.
BEGIN;

CREATE TABLE "audit_log" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "role" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "target_type" TEXT,
    "target_id" TEXT,
    "details" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id")
);

CREATE TABLE "auth_events" (
    "id" TEXT,
    "merchant" TEXT,
    "username" TEXT,
    "user_id" TEXT,
    "action" TEXT NOT NULL,
    "details" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id")
);

CREATE TABLE "auth_sessions" (
    "token_hash" TEXT,
    "merchant" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "expires" TEXT NOT NULL,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("token_hash")
);

CREATE TABLE "branch_price_history" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "product" TEXT NOT NULL,
    "location_id" TEXT NOT NULL,
    "base_price" BIGINT,
    "logistics" BIGINT NOT NULL DEFAULT 0,
    "handling" BIGINT NOT NULL DEFAULT 0,
    "miscellaneous" BIGINT NOT NULL DEFAULT 0,
    "adjustment" BIGINT NOT NULL DEFAULT 0,
    "final_price" BIGINT NOT NULL,
    "min_price" BIGINT,
    "max_price" BIGINT,
    "reason" TEXT,
    "changed_by" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id")
);

CREATE TABLE "branch_prices" (
    "merchant" TEXT NOT NULL,
    "product" TEXT NOT NULL,
    "location_id" TEXT NOT NULL,
    "logistics" BIGINT NOT NULL DEFAULT 0,
    "handling" BIGINT NOT NULL DEFAULT 0,
    "miscellaneous" BIGINT NOT NULL DEFAULT 0,
    "adjustment" BIGINT NOT NULL DEFAULT 0,
    "final_price" BIGINT NOT NULL,
    "min_price" BIGINT,
    "max_price" BIGINT,
    "reason" TEXT,
    "updated_by" TEXT,
    "updated" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("merchant", "product", "location_id")
);

CREATE TABLE "customers" (
    "merchant" TEXT NOT NULL,
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    PRIMARY KEY ("merchant", "id")
);

CREATE TABLE "daily_closings" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "location_id" TEXT NOT NULL,
    "business_date" TEXT NOT NULL,
    "reconciliation_session_id" TEXT NOT NULL,
    "expected_collections" BIGINT NOT NULL DEFAULT 0,
    "presented_collections" BIGINT NOT NULL DEFAULT 0,
    "matched_amount" BIGINT NOT NULL DEFAULT 0,
    "variance" BIGINT NOT NULL DEFAULT 0,
    "closing_status" TEXT NOT NULL DEFAULT 'submitted',
    "closing_note" TEXT,
    "submitted_by" TEXT,
    "submitted_at" TEXT DEFAULT CURRENT_TIMESTAMP,
    "approved_by" TEXT,
    "approved_at" TEXT,
    PRIMARY KEY ("id"),
    UNIQUE ("merchant", "location_id", "business_date")
);

CREATE TABLE "entries" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "kind" TEXT NOT NULL,
    "customer" TEXT NOT NULL,
    "description" TEXT NOT NULL,
    "total" BIGINT NOT NULL,
    "paid" BIGINT NOT NULL,
    "status" TEXT NOT NULL,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    "confirmed" TEXT,
    "customer_credit" BIGINT NOT NULL DEFAULT 0,
    "location_id" TEXT,
    "created_by" TEXT,
    "confirmed_by" TEXT,
    "expected_unit_price" BIGINT,
    "expected_total" BIGINT,
    "price_variance" BIGINT,
    "pricing_source" TEXT,
    PRIMARY KEY ("id"),
    UNIQUE ("merchant", "source")
);

CREATE TABLE "inbox" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "provider_id" TEXT NOT NULL,
    "sender" TEXT NOT NULL,
    "receiver" TEXT NOT NULL,
    "body" TEXT NOT NULL,
    "channel" TEXT NOT NULL,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id"),
    UNIQUE ("provider_id")
);

CREATE TABLE "inbox_claims" (
    "inbox_id" TEXT,
    "target_kind" TEXT NOT NULL,
    "target_id" TEXT NOT NULL,
    PRIMARY KEY ("inbox_id")
);

CREATE TABLE "locations" (
    "merchant" TEXT NOT NULL,
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    PRIMARY KEY ("merchant", "id")
);

CREATE TABLE "payment_records" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "entry_id" TEXT,
    "location_id" TEXT NOT NULL,
    "method" TEXT NOT NULL,
    "amount" BIGINT NOT NULL,
    "institution" TEXT,
    "reference" TEXT,
    "payment_date" TEXT,
    "status" TEXT NOT NULL DEFAULT 'presented',
    "submitted_by" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id"),
    UNIQUE ("merchant", "reference")
);

CREATE TABLE "products" (
    "merchant" TEXT NOT NULL,
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "unit" TEXT NOT NULL,
    "base_selling_price" BIGINT,
    PRIMARY KEY ("merchant", "id")
);

CREATE TABLE "reconciliation_matches" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "session_id" TEXT NOT NULL,
    "payment_id" TEXT NOT NULL,
    "entry_id" TEXT NOT NULL,
    "matched_amount" BIGINT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'matched',
    "created_by" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id"),
    UNIQUE ("session_id", "payment_id", "entry_id")
);

CREATE TABLE "reconciliation_sessions" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "location_id" TEXT,
    "period_start" TEXT NOT NULL,
    "period_end" TEXT NOT NULL,
    "expected_collections" BIGINT NOT NULL DEFAULT 0,
    "presented_collections" BIGINT NOT NULL DEFAULT 0,
    "matched_amount" BIGINT NOT NULL DEFAULT 0,
    "variance" BIGINT NOT NULL DEFAULT 0,
    "status" TEXT NOT NULL DEFAULT 'open',
    "created_by" TEXT,
    "approved_by" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    "approved" TEXT,
    "exception_reason" TEXT,
    PRIMARY KEY ("id")
);

CREATE TABLE "reversals" (
    "entry_id" TEXT,
    "reason" TEXT NOT NULL,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("entry_id")
);

CREATE TABLE "shops" (
    "id" TEXT,
    "name" TEXT NOT NULL,
    PRIMARY KEY ("id")
);

CREATE TABLE "stock_moves" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "product" TEXT NOT NULL,
    "quantity" BIGINT NOT NULL,
    "kind" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "entry_id" TEXT,
    "reason" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    "cost_total" BIGINT,
    "supplier" TEXT,
    "destination" TEXT,
    "counted_quantity" BIGINT,
    "baseline_quantity" BIGINT,
    "location_id" TEXT,
    PRIMARY KEY ("id"),
    UNIQUE ("merchant", "source"),
    UNIQUE ("entry_id")
);

CREATE TABLE "stock_transfers" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "product" TEXT NOT NULL,
    "quantity" BIGINT NOT NULL,
    "from_location" TEXT NOT NULL,
    "to_location" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "cost_total" BIGINT,
    "reason" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id"),
    UNIQUE ("merchant", "source")
);

CREATE TABLE "suppliers" (
    "merchant" TEXT NOT NULL,
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    PRIMARY KEY ("merchant", "id")
);

CREATE TABLE "user_locations" (
    "merchant" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "location_id" TEXT NOT NULL,
    PRIMARY KEY ("merchant", "user_id", "location_id")
);

CREATE TABLE "users" (
    "merchant" TEXT NOT NULL,
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "username" TEXT NOT NULL,
    "password_salt" TEXT NOT NULL,
    "password_hash" TEXT NOT NULL,
    "role" TEXT NOT NULL,
    "active" BIGINT NOT NULL DEFAULT 1,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    "must_change_password" BIGINT NOT NULL DEFAULT 0,
    "updated" TEXT,
    PRIMARY KEY ("merchant", "id")
);

CREATE TABLE "voice_jobs" (
    "id" TEXT,
    "merchant" TEXT NOT NULL,
    "provider_id" TEXT NOT NULL,
    "sender" TEXT NOT NULL,
    "receiver" TEXT NOT NULL,
    "media_id" TEXT NOT NULL,
    "mime_type" TEXT,
    "status" TEXT NOT NULL,
    "transcript" TEXT,
    "inbox_id" TEXT,
    "error" TEXT,
    "created" TEXT DEFAULT CURRENT_TIMESTAMP,
    "updated" TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id"),
    UNIQUE ("provider_id")
);

CREATE TABLE "whatsapp_reply_outbox" (
    "provider_key" TEXT,
    "sender" TEXT NOT NULL,
    "receiver" TEXT NOT NULL,
    "state" TEXT NOT NULL DEFAULT 'pending',
    "last_error" TEXT,
    "created_at" TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "reply_text" TEXT,
    PRIMARY KEY ("provider_key")
);

CREATE INDEX "idx_daily_closings_location_date" ON "daily_closings" ("merchant", "location_id", "business_date");
CREATE INDEX "idx_daily_closings_merchant_date" ON "daily_closings" ("merchant", "business_date");
CREATE INDEX "idx_payment_records_merchant_entry" ON "payment_records" ("merchant", "entry_id");
CREATE INDEX "idx_payment_records_merchant_location" ON "payment_records" ("merchant", "location_id");
CREATE INDEX "idx_recon_matches_session" ON "reconciliation_matches" ("session_id");
CREATE INDEX "idx_recon_sessions_merchant_period" ON "reconciliation_sessions" ("merchant", "period_start", "period_end");


-- Case-insensitive username uniqueness
CREATE UNIQUE INDEX "uq_users_username_lower"
ON "users" (lower("username"));

COMMIT;
