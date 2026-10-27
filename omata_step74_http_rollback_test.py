"""Step 74: isolated HTTP -> adapter -> Neon testing branch; all writes rolled back.
Uses a test-only dispatcher to keep several HTTP calls in ONE DB transaction.
Does not enable the real staging write flag or call the committing wrapper.
"""
import getpass
import http.client
import json
import threading
import uuid
from contextlib import closing
from urllib.parse import urlsplit
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
import app
import omata_pg_http_finance as pg_http
from omata_pg_finance_adapter import draft_with_existing_connection, confirm_with_existing_connection
from omata_pg_finance_read import finance_snapshot
from omata_pg_finance import MAIN_LOCATION_ID

HOST = "ep-dawn-night-b9t7gdwg.c-7.eu-central-1.aws.neon.tech"
print("=== OMATA STEP 74 HTTP/NEON ROLLBACK TEST ===")
url = getpass.getpass("Paste omata-finance-testing URL (hidden): ").strip().strip('"').strip("'")
p = urlsplit(url)
if (p.scheme not in ("postgres", "postgresql") or p.hostname != HOST
        or p.path != "/neondb" or not p.username or not p.password):
    raise SystemExit("STOP: Wrong Neon testing branch URL")
db = server = thread = None
passed = False
rolled_back = False
try:
    db = psycopg.connect(url, sslmode="require", connect_timeout=20, autocommit=False, row_factory=dict_row)
    with db.cursor() as cur:
        cur.execute("""SELECT c.merchant, c.id AS customer FROM customers c
                        JOIN shops s ON s.id=c.merchant
                        WHERE c.id<>%s ORDER BY c.merchant,c.id LIMIT 1""",
                    ("walk-in-cash-customer",))
        fixture = cur.fetchone()
        if not fixture:
            raise RuntimeError("No test customer found")
        merchant, customer = fixture["merchant"], fixture["customer"]
        cur.execute("SELECT id FROM shops WHERE id<>%s ORDER BY id LIMIT 1", (merchant,))
        other = cur.fetchone()
        if not other:
            raise RuntimeError("Second merchant not found")
        other_merchant = other["id"]
        cur.execute("""SELECT COALESCE(SUM(total-paid+COALESCE(customer_credit,0)),0) AS debt
                       FROM entries WHERE merchant=%s AND customer=%s AND status='confirmed'""",
                    (merchant, customer))
        debt_before = cur.fetchone()["debt"]
    owner = dict(id="step74-owner",role="owner",merchant=merchant,must_change_password=False)
    outsider = dict(id="step74-outsider",role="owner",merchant=other_merchant,must_change_password=False)
    tokens = {"step74-owner-token": owner, "step74-outsider-token": outsider}
    def fake_auth(token):
        return tokens.get(token)
    def rollback_only_finance(action, payload, actor):
        # This test-only dispatcher uses the REAL adapter and finance SQL,
        # but deliberately avoids the application's db.commit() wrapper.
        if action == "draft":
            ident = draft_with_existing_connection(db, payload, actor)
            return {"id": ident, "status": "draft", "database": "neon-finance-testing"}
        if action == "confirm":
            if payload.get("human_confirmed") is not True:
                raise PermissionError("Explicit human confirmation required")
            result = confirm_with_existing_connection(db, str(payload.get("id") or ""), actor)
            return {"ok": True, "confirmed_now": bool(result), "database": "neon-finance-testing"}
        raise ValueError("Invalid action")
    server = __import__("http.server",fromlist=["HTTPServer"]).HTTPServer(("127.0.0.1",0),app.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    def post(path, body, token=None):
        headers = {"Content-Type":"application/json"}
        if token: headers["X-Session-Token"] = token
        with closing(http.client.HTTPConnection("127.0.0.1",port,timeout=10)) as conn:
            conn.request("POST",path,body=json.dumps(body).encode(),headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode())
    source = "step74-" + uuid.uuid4().hex
    data = dict(merchant=merchant, source=source,kind="sale",customer=customer,
                description="Rollback-only HTTP integration test",total="100.00",
                paid="75.00",location_id=MAIN_LOCATION_ID)
    with patch.object(app,"authenticate_token",fake_auth), patch.object(pg_http,"execute_staging_finance",rollback_only_finance):
        thread.start()
        draft_path = "/api/staging/finance-drafts"
        confirm_path = "/api/staging/finance-confirm"
        status,_ = post(draft_path,data)
        assert status==401, f"No-auth HTTP status: {status}"
        print("PASS: Unauthenticated draft rejected (401)")
        status,result = post(draft_path,data,"step74-owner-token")
        assert status==200 and result.get("status")=="draft", f"Draft status: {status}"
        entry_id = result["id"]
        print("PASS: Authenticated HTTP sale draft")
        with db.cursor() as cur:
            cur.execute("SELECT status,total,paid FROM entries WHERE merchant=%s AND id=%s",(merchant,entry_id))
            row=cur.fetchone()
            assert row and row["status"]=="draft" and row["total"]==10000 and row["paid"]==7500
            cur.execute("""SELECT COALESCE(SUM(total-paid+COALESCE(customer_credit,0)),0) AS debt
                           FROM entries WHERE merchant=%s AND customer=%s AND status='confirmed'""",
                        (merchant,customer))
            assert cur.fetchone()["debt"] == debt_before
        print("PASS: Draft has no confirmed financial effect")
        status,_=post(confirm_path,{"merchant":merchant,"id":entry_id},"step74-owner-token")
        assert status==403, f"Missing human confirmation status: {status}"
        print("PASS: Confirmation without explicit approval rejected (403)")
        # Other merchant's own merchant value passes the HTTP merchant check,
        # but cannot confirm the owner's record in PostgreSQL.
        status,_=post(confirm_path,{"merchant":other_merchant,"id":entry_id,"human_confirmed":True},"step74-outsider-token")
        assert status in (400,403), f"Cross-merchant status: {status}"
        with db.cursor() as cur:
            cur.execute("SELECT status FROM entries WHERE id=%s",(entry_id,))
            assert cur.fetchone()["status"]=="draft"
        print("PASS: Cross-merchant confirmation rejected; draft preserved")
        status,result=post(confirm_path,{"merchant":merchant,"id":entry_id,"human_confirmed":True},"step74-owner-token")
        assert status==200 and result.get("confirmed_now") is True, f"Authorized confirmation: {status}"
        print("PASS: Explicit human confirmation posted once")
        status,result=post(confirm_path,{"merchant":merchant,"id":entry_id,"human_confirmed":True},"step74-owner-token")
        assert status==200 and result.get("confirmed_now") is False, f"Duplicate status: {status}"
        print("PASS: Duplicate confirmation idempotent")
        snapshot = finance_snapshot(db,actor=owner)
        assert any(e["id"]==entry_id and e["status"]=="confirmed" for e in snapshot["entries"])
        print("PASS: PostgreSQL read reflects confirmed entry")
    db.rollback()
    rolled_back=True
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM entries WHERE merchant=%s AND source=%s",(merchant,source))
        assert cur.fetchone()["n"]==0
        cur.execute("SELECT COUNT(*) AS n FROM audit_log WHERE target_id=%s",(entry_id,))
        assert cur.fetchone()["n"]==0
    print("PASS: All test entries and audit records removed by rollback")
    passed=True
    print("\nSTEP 74: PASS")
except Exception as exc:
    print("\nSTEP 74: FAILED")
    print("Error type:",type(exc).__name__)
    print("Error:",str(exc)[:240])
finally:
    if server:
        server.shutdown()
        server.server_close()
    if thread and thread.is_alive():
        thread.join(timeout=5)
    if db:
        db.rollback()
        rolled_back=True
        db.close()
    url=None
    print("Rollback completed:",rolled_back)
    print("All checks passed:",passed)
    print("Live SQLite changed: NO")
    print("Staging write feature enabled: NO")
