# Paris Sender V10 — Migration Plan

**Status:** Phase 12 complete. Companion to `audit_report.md`, `PHASE12_AUDIT_REPORT.md`, and `FINAL_SYSTEM_REPORT.md`.
**Goal:** Transform Paris Sender from the retired desktop monolith into a modular, service-oriented desktop app: **Electron frontend → FastAPI → service layer → providers**, Python backend preserved, SQLite now / PostgreSQL-ready.

This plan describes *what* each phase does and its exit gate. It does not prescribe implementation code. **No phase may be skipped, and no phase starts until the previous phase passes its quality gate.**

---

## Guiding Constraints

- **Preserve autograb** (`[firstname]`/`[greetings]`/`[company]`, Jinja2 context, ISP fallbacks). Do not rewrite its logic unless a bug is found; only adapt its interface.
- **Preserve validated behaviors** in the pytest suite (template/Jinja validation, DNS classification, sent-log behavior, URL-hostname sanitization, host-key policy).
- **Smallest correct change per step.** The strangler-fig migration is complete: the new architecture is active and the legacy desktop fallback has been removed.

---

## Target Architecture

```
frontend/electron/        # UI (Dashboard, Campaigns, Compose, Contacts,
                          #     Deliverability, DNS, Domain Manager, Warmup,
                          #     Analytics, Logs, Health Monitor, Settings)
backend/
  api/                    # FastAPI routers (the only entry point for the UI)
  services/              # DeliveryService, WarmupService, LoggingService, ...
  models/               # ORM entities (ledger, domains, ...)
  workers/             # background send/warmup/health jobs
  repositories/        # data access (SQLite now, Postgres-ready)
  validators/          # template/Jinja/placeholder/DNS validators
deliverability/         # score engine, seed/inbox/link/auth checks
dns/                    # DKIM/SPF/DMARC generation + verification
warmup/                 # ramp schedules, limits
ledger/                 # transactional email event store
logging/                # structured logging service
tests/                  # unit / integration / service / api / deliverability / ui
docs/                   # architecture, manuals, reports
```

**Delivery flow (mandatory):** `UI → API → DeliveryService → Provider`. No delivery logic in the UI.

---

## Phase Roadmap

### Phase 1 — Project Audit ✅ (this PR)
- Deliverables: `audit_report.md`, `migration_plan.md`.
- Gate: documents reviewed/approved before Phase 2.

### Phase 2 — UI Migration (desktop UI → Electron) ✅
- Scaffold `frontend/electron/` (use an official scaffolding tool, not hand-rolled).
- Build the 12 required screens against the FastAPI surface; **Compose** must support HTML + plain text, auto HTML/plain previews, template/placeholder/Jinja validation, character count, and spam-warning indicators.
- Remove obsolete/dead/duplicate compose controls and legacy sender options.
- Retired the desktop fallback in Phase 12 after parity was verified.
- Gate: screens render against API; compose previews + validation work; validation tests pass.

### Phase 3 — Delivery Architecture Refactor
- Introduce `DeliveryService` abstraction + `Provider` interface; implement `SMTPDeliveryProvider` first.
- Route all sending through `UI → API → DeliveryService → Provider`.
- Consolidate the duplicated MIME/send paths (SMTP/VPS/MX/Outlook) behind the service.
- Gate: HTML and plain-text sends both work through the new path; autograb intact; send tests pass.

### Phase 4 — Domain Management
- `Domain Manager`: add/validate domain; generate DKIM/SPF/DMARC; verify DNS; track health, reputation, warmup, and auth status (persisted, not in `tk.*Var`).
- Gate: DNS generation/verification covered by tests; domain records persisted.

### Phase 5 — Email Ledger
- Add ledger entities: `Campaign`, `Message`, `Recipient`, `Event`, `Bounce`, `Open`, `Click`, `Unsubscribe`.
- Status set: `QUEUED, PROCESSING, SENT, DELIVERED, OPENED, CLICKED, BOUNCED, FAILED, UNSUBSCRIBED`.
- Every send event is recorded — no exceptions. DeliveryService writes through repositories.
- Gate: every send produces ledger rows; analytics read from the ledger.

### Phase 6 — Deliverability Suite
- Enhance seed checking, inbox-placement verification, link/domain health, auth verification, warmup verification, spam-content analysis.
- Implement deliverability **score engine**, domain-health dashboard, campaign-risk dashboard.
- Extract autograb intact into a service here; keep `TestContextPreview` green.
- Gate: deliverability suite + score engine covered by tests and surfaced in UI.

### Phase 7 — Warmup Engine
- Refactor existing smart warmup into `WarmupService` (daily limits, ramp schedules, domain/reputation tracking, health, scheduling). Preserve current behavior.
- Gate: warmup parity with `warmup_schedule.json` behavior; service tests pass.

### Phase 8 — Health Monitor
- Expand the existing monitor to track queue depth, campaign status, DNS/DKIM/SPF/DMARC status, DB health, worker health, memory/CPU, background jobs.
- Gate: health endpoints + dashboard reflect live metrics.

### Phase 9 — Logging
- Replace fragmented `self.log(...)` calls with a `LoggingService`: structured logs, severity levels, search, filtering, export, audit trail.
- Preserve `TestSentEmailLog` semantics (severity classification, cap, filter, CSV export).
- Gate: logging unified; log tests pass; export works.

### Phase 10 — Security
- **Rotate the exposed Fernet key immediately**; purge `encryption.key` and `paris_sender.log` from the tree (and history); add `.gitignore`.
- Remove hardcoded secrets/credentials; move secrets to env vars / OS keyring / encrypted config.
- Add secret scanning, configuration validation, and startup security checks. Harden TLS (remove insecure-SSL downgrade) and SSH host-key policy.
- Gate: secret scan clean; no secrets in tree; startup checks enforce safe config.

### Phase 11 — Testing ✅
- Build unit / integration / deliverability / service / API / UI test suites; preserve migrated behavior in pytest.
- Add lint (ruff/flake8), type-check (mypy), dependency audit (pip-audit), dead-code scan (vulture) to CI.
- Gate: full suite + static gates green.

### Phase 12 — Cleanup ✅
- Removed obsolete desktop monolith files and the retired source-inspection unittest suite.
- Added non-SMTP provider selection through the same DeliveryService/ledger/logging flow as SMTP.
- Gate: backend pytest suite green; no active Python code imports desktop UI modules.

---

## Cross-Phase Quality Gate (run before each phase completes)

1. Run all tests. 2. Lint. 3. Static analysis. 4. Dependency audit. 5. Dead-code scan.
6. Verify autograb works. 7. HTML send works. 8. Plain-text send works. 9. Compose preview works. 10. Deliverability suite works.

If any item fails: **STOP → fix → retest → repeat.** Only then continue.

---

## Final Deliverables (produced across the phases)

`architecture.md`, `migration_report.md`, `removed_features.md`, `new_features.md`, `test_report.md`, `security_report.md`, `deliverability_report.md`, `deployment_guide.md`, `operator_manual.md`.

---

## Immediate Recommended Next Actions (entering Phase 10 early for the critical item)

Because the committed Fernet key is a **critical** exposure, the key rotation/purge from Phase 10 should be pulled forward and executed as the first code change after this audit is approved, independent of the UI migration timeline.
