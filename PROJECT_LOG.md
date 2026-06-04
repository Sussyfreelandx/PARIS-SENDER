# Paris Sender V10 — Project Log

`AUTO_ADVANCE_PHASES = True`

Per the Phase Gate Override, phases may auto-advance when the previous phase's
artifacts exist and pass structural validation and no critical security issue
blocks the build. Full quality-gate checks run **after** each phase's code
changes. Execution stops only on: test failures, build/compile errors, or
critical security issues.

## Gate log

- **Phase 1 — Project Audit:** artifacts `audit_report.md`, `migration_plan.md`
  present and structurally valid (required sections present). Baseline:
  `paris_sender_complete1.py` compiles; `test_fixes.py` = 216 passed / 1 skipped.
  → **Phase 1 gate validated.**
- **Critical security blocker (Phase 10 item pulled forward):** the audit found a
  committed Fernet key (`encryption.key`) and a committed runtime log
  (`paris_sender.log`). Per gate rule 1c this blocks auto-advance, so it was
  remediated first:
  - Purged `encryption.key` and `paris_sender.log` from the working tree and index.
  - Added `.gitignore` to prevent re-committing secrets/logs/state/db.
  - Hardened the key loader to resolve from `PARIS_SENDER_ENCRYPTION_KEY` env var
    first, then a git-ignored local file, then generate (rotating away from any
    previously exposed key).
  - Re-ran quality gate: compiles; 216 passed / 1 skipped.
  → **Critical security gate cleared. AUTO_ADVANCE unblocked.**
- **Phase 1 gate validated, proceeding to Phase 2+ foundational scaffolding.**
- **Phase 3 (Delivery) + Phase 5 (Ledger) foundations:** built alongside the
  monolith under `backend/` (strangler-fig):
  - `backend/models/ledger.py` — entities (Campaign, Message, Recipient, Event +
    Bounce/Open/Click/Unsubscribe) and the full Status enum (QUEUED, PROCESSING,
    SENT, DELIVERED, OPENED, CLICKED, BOUNCED, FAILED, UNSUBSCRIBED).
  - `backend/repositories/ledger.py` — sqlite3 LedgerRepository (Postgres-ready,
    parameterized SQL, `:memory:` support); every event persisted.
  - `backend/services/delivery.py` — `DeliveryProvider` ABC, `SMTPDeliveryProvider`
    (DI SMTP factory, secure-by-default TLS), and `DeliveryService` orchestrating
    QUEUED→PROCESSING→SENT/FAILED with ledger writes. Flow: UI→API→DeliveryService→Provider.
  - `backend/services/mime.py` — single MIME builder (removes duplication).
  - `backend/validators/autograb.py` — autograb personalization + Jinja2 render,
    parity-tested against `test_fixes.py` behaviors.
  - `backend/api/app.py` — FastAPI app (POST /campaigns, POST /campaigns/{id}/send,
    GET /campaigns/{id}, GET /health) with injectable provider/ledger.
  - `tests/` — 10 new tests (ledger, delivery, autograb, api).
  - Quality gate: all new .py compile; `pytest tests/` = 10 passed;
    `unittest test_fixes.py` = 216 passed / 1 skipped (no regression).
  → **Phase 3/5 foundation gate validated.**
- **Phase 2 — UI Migration (Tkinter → Electron):** scaffolded `electron/`
  (Electron main + secure preload, Vite + React renderer, hot reload via
  `concurrently`/`wait-on`). Built 8 screens (Dashboard, Campaign Manager,
  Compose Editor, Contacts, Analytics, Settings, Logs, Domain Manager) wired to
  FastAPI through `renderer/api/client.js`. Compose editor supports HTML + plain
  text, live autograb-personalized preview (`/compose/preview`), and
  Jinja/placeholder/spam/HTML-ratio validation (`/compose/analyze`); dead Tkinter
  compose features omitted. Campaign send is UI-gated to verified domains and
  passes `html` + non-SMTP delivery flags. New compose-analysis backend
  (`backend/validators/compose.py`) + endpoints. Quality gate: `vite build`
  compiles the renderer (45 modules); `node --check` clean on all plain JS;
  `pytest tests/` green; `test_fixes.py` = 216 pass / 1 skip (no regression).
  Screenshots require a local GUI run (no display in CI). See `PHASE2_LOG.md`.
  → **Phase 2 gate validated (pending local GUI screenshots).**
- **Phase 4 — Domain Management (DKIM/SPF/DMARC):** added
  `backend/models/domain.py`, `backend/repositories/domain.py` (DomainRepository +
  health history), and `backend/services/domain.py` (RSA-2048 DKIM key gen,
  SPF/DMARC builders, injectable DNS verification, 0–100 health scoring). FastAPI
  domain endpoints (add/list/get/verify/dmarc/dkim-rotate/delete/history) and an
  Electron Domain Manager wizard. Campaign send enforces verified sender domains
  (backward compatible for unmanaged domains). Quality gate: `pytest tests/` =
  all green (incl. `test_domain.py`, `test_domain_api.py`); no delivery
  regression. See `PHASE4_LOG.md`.
  → **Phase 4 gate validated.**

## Remaining work (subsequent phases, async)

Phase 12 completed the strangler-fig migration: the Electron/FastAPI/service
architecture is active, non-SMTP selection routes through `DeliveryService`, and
the retired desktop monolith/source-inspection suite were removed from the tree.

## Notes

- Phase 12 retired the desktop monolith and the legacy source-inspection unittest suite.
- Autograb and migrated validation/delivery behaviors are preserved in `tests/`.
