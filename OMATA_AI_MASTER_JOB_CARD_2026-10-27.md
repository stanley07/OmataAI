# OMATA AI — MASTER JOB CARD / PLATFORM-INDEPENDENT EXECUTION PLAN

**Job card ID:** OMATA-HACKATHON-2026  
**Version:** 1.0  
**Updated:** 27 October 2026 (Africa/Lagos)  
**Owner:** Omata AI project team  
**Goal:** Deliver an end-to-end, demonstrable, Meta-model-powered, WhatsApp-native business operations pilot for the hackathon, without jeopardizing operational merchant records; complete comprehensive production hardening after the demo.  
**Operating rule:** One job card at a time; evidence before PASS; do not rebuild working features; retain human-review-first financial posting.

## A. Immutable project context and safety boundaries

- **GitHub:** `https://github.com/stanley07/OmataAI` (reported push successful). Branches `integration/security-a8h` and `hackathon/meta-ai-2026` both point to verified commit `d899b41` at branch creation. Latest branch in user's screenshot: `hackathon/meta-ai-2026`.
- **Local isolated repo:** `C:\Users\ezebo\Downloads\Voice-Ledger-Milestone-1\voice-ledger-postgres\isolated_integration_12cq_a8h\working`.
- **Local project root (additional modules):** `C:\Users\ezebo\Downloads\Voice-Ledger-Milestone-1\voice-ledger-postgres`.
- **Operational DB—NO synthetic writes:** `C:\Users\ezebo\Downloads\Voice-Ledger-Milestone-1\voice-ledger\ledger.sqlite3`. Treat another root `voice-ledger-postgres\ledger.sqlite3` as protected until positively classified.
- **Known running services (historical, reverify before use):** dashboard `127.0.0.1:8080`; WhatsApp receiver `127.0.0.1:8081`; staging `127.0.0.1:8090`. Never stop/restart/repoint these without explicit authorization.
- **Domain:** `https://omataai.com.ng` for official website; proposed `app.omataai.com.ng` for authenticated dashboard; existing `voice.omataai.com.ng/webhook` configuration must be inspected, not overwritten based on assumptions.
- **Code privacy:** repository intended private; verify private visibility on GitHub. Never commit `.env`, API keys, Meta tokens, phone numbers/customer records, DB files, backups, downloaded voice recordings, production logs or secrets in code/history.
- **Development isolation:** disposable synthetic database and test credentials, local-only listener or tightly access-controlled demo URL. Do not open/connect the production database from test harnesses.
- **Financial safety:** AI outputs are proposals; ledger/stock/receivable writes require authorized human confirmation, idempotency, tenant authorization and audit trail.
- **Proof rule:** PASS requires actual command output/test evidence; `TODO`, planned code, or a source inspection alone is not PASS.

## B. Status legend

- `PASS` = evidenced/verified for specified scope only
- `READY` = next task, prerequisites known
- `TODO` = not started/not independently verified
- `BLOCKED` = dependency absent, decision or issue outstanding
- `RETEST` = historical implementation exists but current integrated build needs verification
- `DEFERRED` = deliberately after demonstration; not a waiver of baseline safety

## C. Master execution register

| ID | Priority | Job / deliverable | Current status | Acceptance / evidence required | Dependencies |
|---|---|---|---|---|---|
| OM-00 | P0 | Establish version-controlled hackathon baseline and safety snapshot | PASS | GitHub push of `d899b41` and tracking branches evidenced; four tracked files: `.gitignore`, `app.py`, `dashboard.html`, `voice.py` | None |
| OM-01 | P0 | Inventory all relevant source modules, dependencies, frontends and storage boundaries | READY | Filename/function manifest; local-vs-GitHub source map; missing module list; **no opening operational DB** | OM-00 |
| OM-02 | P0 | Build reproducible isolated demo workspace | TODO | Fresh checkout installs using pinned dependencies, starts on safe local port, synthetic DB only, documented commands | OM-01 |
| OM-03 | P0 | Consolidate needed functionality into `hackathon/meta-ai-2026` without rewriting working modules | TODO | Source-only commit; import/syntax/startup checks; secret scan; no DB/media dumps | OM-01, OM-02 |
| OM-04 | P0 | Establish real Meta-model integration | TODO | Identify eligible/available Meta model and approved API; model identifier/configuration; response trace and reproducible unit tests; no claim based solely on WhatsApp | OM-02 |
| OM-05 | P0 | WhatsApp text intake → protected merchant route → parse draft | RETEST | Actual test message or safe simulated signed webhook yields correctly routed draft once; authenticated/authorized merchant | OM-02, OM-03 |
| OM-06 | P0 | WhatsApp voice/audio → transcription → draft | RETEST | Test audio processed; transcript quality documented; time/size cap, duplicate protection, failed-audio response | OM-02, OM-03 |
| OM-07 | P0 | Meta AI structured extraction and safe validation | TODO | Deterministic schema (sale/payment/stock/etc.); product, qty, gross, amount paid and receivable; reject uncertainty; test real Nigerian English/Pidgin/Igbo-influenced examples without unsupported claims | OM-04, OM-05, OM-06 |
| OM-08 | P0 | Interactive review/approve/correct/reject cycle | RETEST | Authorized reviewer sees draft; no mutation on draft; correction and rejection work; replay denied | OM-07 |
| OM-09 | P0 | Confirmed sale → atomic financial ledger mutation | RETEST | Single approved sale persists exactly once; no unapproved mutation; audit and idempotency | OM-08 |
| OM-10 | P0 | Inventory decrement and stock visibility | RETEST | Approved sale updates correct merchant product/branch; quantity matches; rejects inadequate stock per product policy | OM-09 |
| OM-11 | P0 | Partial payment → customer receivable / debt | RETEST | Synthetic example ₦135,000 sale, ₦115,000 paid, ₦20,000 outstanding; no duplicate charge | OM-09 |
| OM-12 | P0 | Dashboard reflects transaction and supports merchant review | RETEST | Logged-in merchant sees ledger, inventory, debts, status and review; refresh matches backend | OM-08–OM-11 |
| OM-13 | P0 | Outbound WhatsApp acknowledgement | RETEST | Using approved demo number/config, approved state produces a reply, delivery/errors logged; no live sends without authorization | OM-05, OM-08 |
| OM-14 | P0 | End-to-end showcase, including failures | TODO | One uninterrupted safe demo: WhatsApp text/voice → Meta AI interpretation → approval → sale → stock → receivable → dashboard → acknowledgement; duplicate/reject and wrong-merchant tests | OM-04–OM-13 |
| OM-15 | P0 | Non-negotiable baseline isolation/auth protections for internet demo | RETEST | Authenticated access, per-merchant authorization, origin/session safety, secrets out of Git, synthetic DB, controlled endpoint; deny cross-merchant actions | OM-02, OM-03 |
| OM-16 | P1 | Demo presentation polish and narrative | TODO | Readable dashboard with seeded synthetic merchants, live-looking but clearly described demonstration data; no fake performance claims | OM-12, OM-14 |
| OM-17 | P1 | Model evaluation/evidence | TODO | Labeled sample set, extraction accuracy, error cases, latency, model use evidence, comparison to prior parser | OM-07 |
| OM-18 | P1 | Deployment/DNS audit and controlled demo URL | TODO | Read-only map of domain, hosting, DNS, webhook and TLS; no changes to operational receiver; safe separate demo or screenshare | OM-02, OM-15 |
| OM-19 | P1 | GitHub handoff package | TODO | README, architecture, prerequisites, environment example placeholders, setup/test/demo instructions, test results and commit references | OM-03, OM-14 |
| OM-20 | P1 | Hackathon submission package | TODO | Official eligibility/submission checklist confirmed; pitch, short demo video, model attribution, project link, technical evidence, screenshots | OM-14, OM-17, OM-19 |
| OM-21 | P2 | A8H.29D centralized HTTP wrapper integration | BLOCKED/UNVERIFIED | No execution output received for supplied patch; A8B wrapper preservation and HTTP regression required | OM-03 |
| OM-22 | P2 | Targeted customer endpoint/tenant authorization patch | TODO | All customer reads/writes bound to authenticated merchant; cross-tenant negatives, no information leakage | OM-21 |
| OM-23 | P2 | Full integrated HTTP and database failure regression | TODO | Disposable DB + ephemeral loopback ports; positive/negative auth tests; staging routes preserved; no live service connections | OM-21, OM-22 |
| OM-24 | P2 | Production backup/migration/observability/release gates | DEFERRED | Verified backup+restore, migration/rollback, audit/monitoring, secret rotation, runtime mapping, release approval | OM-23 |
| OM-25 | P2 | Controlled production rollout | BLOCKED | Explicit owner authorization and all release gates passed; no implied deployment from Git push | OM-24 |

## D. Verified security checkpoints (do not lose)

- `b66323c`: sanitized newer application baseline (four tracked files as of publication).
- `6b9a6ca`: authenticated merchant binding hardening.
- `197d3b1`: reject unknown roles during session auth.
- `d899b41`: secure merchant-specific expiring one-time owner invitations.
- Historical **A8H.25H / 26B / 27B / 27H** isolated invitation/registration/database/auth tests passed, including replay, expiry, concurrency, rollback and disposable full-schema authentication test. These do **not** prove the final integrated HTTP stack passed.
- **A8H.29C** read-only HTTP boundary inspection passed; discovered integrated Handler lacks named `_validate_post_origin` and `_handle_sqlite_error` wrappers found in A8B.
- **A8H.29D** patch supplied but execution not evidenced. Do not mark complete without output.
- **A8H.30** preliminary credential-pattern scan passed: 4 commits, 7 unique file versions; not a complete privacy/security guarantee.
- **A8H.32** GitHub push of `integration/security-a8h` evidenced by user screenshot.
- **A8H.33** branch `hackathon/meta-ai-2026` created and pushed at `d899b41` evidenced by user screenshot.

## E. Work sequencing and demo acceptance

**Phase 1 — Make it runnable:** OM-01 → OM-02 → OM-03; establish safe isolated dataset and startup script.

**Phase 2 — Make it intelligent:** OM-04 → OM-07, using OM-05/06 intake and real Meta model; do not mistake a rule parser or WhatsApp Business API for a Meta foundation-model integration.

**Phase 3 — Make it valuable:** OM-08 → OM-13; one merchant-approved transaction must update sales, inventory and debt, then dashboard and WhatsApp acknowledgement.

**Phase 4 — Prove and submit:** OM-15 (baseline guardrails) + OM-14 → OM-16–20; capture reproducible demonstration, test data, measured model results and submission evidence.

**Phase 5 — Harden production:** OM-21–25 after hackathon demo path is stable, with essential protections retained at all times.

**Demo sign-off checklist** (all must be evidenced, not assumed):
- [ ] Fresh clone and documented install/startup succeeds without operational data.
- [ ] Meta model genuinely used; model responses logged safely with a traceable test.
- [ ] WhatsApp text message and voice note lead to correct proposed transaction.
- [ ] Authorized merchant reviews and confirms or corrects; rejected draft creates no ledger mutation.
- [ ] Confirmed example: 3 rice bags, ₦135,000 sale, ₦115,000 paid, ₦20,000 receivable; stock -3.
- [ ] Duplicate/replay produces zero additional financial effects.
- [ ] Second merchant is denied access to first merchant's data.
- [ ] Dashboard and acknowledgement reflect the same committed transaction.
- [ ] Demo video and GitHub README precisely reproduce the flow.

## F. Mandatory evidence per job card

After completing each job, report exactly:

```
JOB CARD: OM-XX
STATUS: PASS | FAIL | BLOCKED | RETEST
BRANCH / COMMIT: ...
FILES CHANGED: ...
COMMANDS RUN: ...
EVIDENCE: actual test output and/or screenshots
TEST ENVIRONMENT: synthetic / isolated / live (identify explicitly)
LIVE DB ACCESSED: NO / YES (authorization ID)
LIVE SERVICES CHANGED: NO / YES (authorization ID)
RISKS / OPEN ISSUES: ...
NEXT JOB CARD: OM-YY
```

A proposed command is **not** executed work. A code commit is **not** a successful end-to-end test. A pushed branch is **not** a deployed application.

## G. Platform-switch handoff prompt

Copy the block below whenever moving to another ChatGPT chat, Codex, Antigravity, IDE, computer or agent:

> **Continue Omata AI using `OMATA_AI_MASTER_JOB_CARD_2026-10-27.md` as the controlling execution register.** The current GitHub repo is `stanley07/OmataAI`; development branch `hackathon/meta-ai-2026`, last evidenced commit `d899b41`. Two branches pushed successfully. Only four tracked files in the isolated baseline. Prior successful isolated security work must be preserved. Current priority is a complete **Meta-model-powered, WhatsApp voice/text → merchant approval → ledger/inventory/receivables → dashboard** hackathon demo; do not redesign or restart completed work. Preserve the human-review-first financial model. NEVER connect, modify or copy operational DB `C:\Users\ezebo\Downloads\Voice-Ledger-Milestone-1\voice-ledger\ledger.sqlite3` or interrupt running receivers. Use disposable synthetic test data. Next job is **OM-01, read-only functional source inventory**; then OM-02/OM-03. Work one job card at a time, give exact Windows PowerShell commands, request actual outputs, and only mark PASS when verified. Update this job-card file after each milestone and commit the revised file on the hackathon branch once reviewed. If a platform cannot see local files, ask for this file or the latest job card state rather than assuming access.

## H. Change control

- Do not reorder completed evidence or rewrite commit history to make prior work seem newly created.
- Do not mutate live database, service, DNS, Meta numbers/routes, or deploy production without the user's explicit task-specific approval.
- Before every code change: confirm branch, `git status`, required files, test isolation and rollback target.
- At end of every job: report results, update status and next ID; periodically commit the job card so GitHub becomes the portable source of truth.
- Keep a private working log of blockers and decisions; do not include tokens, real merchant phone numbers or sensitive customer data in this document.

