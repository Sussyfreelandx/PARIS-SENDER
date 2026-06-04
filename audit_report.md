# Paris Sender — Phase 1 Project Audit

**Audit date:** 2026-06-03
**Audited revision:** `9f4ff3b` (branch `copilot/paris-sender-refactor`)
**Scope:** Full repository audit as required by Phase 1 of the V10 Refactor Master Instructions. This document is descriptive only — no code is changed in Phase 1. It is the prerequisite gate for Phase 2 onward.

---

## 1. Repository Inventory

| File | Lines | Role |
|------|------:|------|
| `paris_sender_complete1.py` | 12,060 | Entire application: Tkinter UI, delivery engines, DNS, deliverability, warmup, tracking, AI, persistence — one monolith. |
| `test_fixes.py` | 2,119 | `unittest` suite covering validation, DNS, logging, URL sanitization, host-key policy, GUI helpers. |
| `requirements.txt` | 21 deps | Mixed runtime/UI/automation dependencies (Tkinter-era + FastAPI + Selenium + Flask). |
| `README.md` | 0 | Empty. |
| `encryption.key` | 44 bytes | **Committed Fernet key — critical security risk (see §6).** |
| `paris_sender.log` | — | Committed runtime log artifact (should not be in VCS). |

Runtime/state files referenced by the app but not (all) tracked: `DB_FILE` (SQLite), `warmup_schedule.json`, `encryption.key`.

### Top-level structure (classes in the monolith)

| Line | Class | Responsibility |
|-----:|-------|----------------|
| 441 | `ScrollableFrame` | Tkinter widget |
| 506 | `Tooltip` | Tkinter widget |
| 532 | `CollapsibleSection` | Tkinter widget |
| 564 | `DBHandler` | SQLite persistence (2 tables only) |
| 680 | `IMAPHandler` | IMAP reads |
| 744 | `AIHandler` | Remote AI (OpenAI-style) |
| 786 | `LocalAIHandler` | Local AI |
| 823 | `ProxyVPSHandler` | SSH/proxy VPS sending + health |
| 1627 | `DirectMXHandler` | Direct-to-MX sending |
| 2175 | `HTMLHelper` | HTML utilities |
| 2200 | `AsyncConnectionPool` | Async SMTP pooling |
| 2219 | `SMTPHandler` | Classic SMTP sending |
| 2742 | `OutlookCOMHandler` | Outlook COM sending |
| 3012 | `TrackingServer` | Flask open/click tracking |
| 3165 | `DeliverabilityHelper` | Seed/link/DNS checks |
| 3347 | `CoreEngine` | Headless engine |
| 3381 | `BulkEmailSenderAPI` | FastAPI surface |
| 3394 | `BulkEmailSender` | God-object Tk app (~8,600 lines) tying everything together |

---

## 2. Architecture Risks

1. **Single-file monolith (12k lines).** No module boundaries. `BulkEmailSender` is a ~8,600-line god object mixing UI construction, business logic, persistence, networking, and scheduling. Untestable in isolation; high merge-conflict and regression risk.
2. **UI/business-logic coupling.** Delivery is invoked directly from Tkinter callbacks and `tk.*Var` state (`warmup_mode`, sender config, etc.). There is no UI→API→Service→Provider seam — directly contradicts the Phase 3 target architecture.
3. **Four parallel delivery paths** with overlapping logic: `SMTPHandler`, `ProxyVPSHandler` (`send_via_vps` / `_send_via_vps_native`), `DirectMXHandler` (`_send_via_mx`), `OutlookCOMHandler`, plus a module-level `universal_smtp_send`. No unifying `DeliveryService` abstraction.
4. **Anemic persistence.** `DBHandler` defines only two tables (`recipients`, `mx_retry_queue`). There is **no transactional email ledger** (Campaign/Message/Recipient/Event/Bounce/Open/Click/Unsubscribe) as Phase 5 requires; analytics are reconstructed ad hoc.
5. **Configuration/state in loose JSON/files** (`warmup_schedule.json`, encryption key file, log file) rather than a managed config/secret layer.
6. **Optional-dependency soup.** Many `try/except ImportError` capability flags (`FASTAPI_AVAILABLE`, `FLASK_AVAILABLE`, `OUTLOOK_COM_AVAILABLE`, `KEYRING_AVAILABLE`, `TKCALENDAR_AVAILABLE`, `PYNGROK_AVAILABLE`, `WEBDRIVER_MANAGER_AVAILABLE`) gate behavior at runtime, making the effective code path environment-dependent and hard to reason about.
7. **Mixed web stacks.** Flask (+ waitress) is used for the tracking server while FastAPI is used for the API surface — two HTTP frameworks in one process.

---

## 3. Duplicated Code

- **MIME construction duplicated** across at least 3 methods: `SMTPHandler._create_mime_message` (line 2372), `ProxyVPSHandler._create_vps_mime_message` (1398), `DirectMXHandler._create_mime_message` (1757) — 9 references to the two method names in total. Each re-implements headers, body, and attachment encoding.
- **Send logic duplicated** across `run_sending_job` / `send_bulk_emails_threaded` / `send_bulk_emails_async` (SMTP), `run_vps_sending_job` (VPS), `run_direct_mx_sending_job` (MX) — same batching/sender-rotation/logging patterns re-written per path.
- **Repeated inline imports.** `import smtplib` / `import ssl` / `from email.mime.text import MIMEText` re-imported inside multiple methods (lines 934, 962–964, 1216–1219, 1497, 1534–1536) instead of once at module top.
- **Sender-rotation logic** appears in multiple forms (`get_sender_details`, `get_rotated_sender`).
- **DNS/domain extraction** logic appears both in app code and is independently re-tested in `test_fixes.py`.

---

## 4. Dead / Obsolete Code & Workflows

- **Entire Tkinter UI layer** (`ScrollableFrame`, `Tooltip`, `CollapsibleSection`, and ~20 `_build_*_tab` methods) is obsolete under the Electron target and will be removed in Phase 2/12.
- **Versioned changelog comments** at the top of the file (FIX v8.0.2, v9.0.0, RESTORE, ENHANCEMENT…) describe historical fixes and reference behaviors that should move to `CHANGELOG`/docs.
- **Selenium / undetected-chromedriver / webdriver-manager** automation stack: heavy browser-automation dependencies whose runtime reachability is unclear; flagged for reachability analysis before removal.
- **Outlook COM path** is platform-gated (Windows only) and likely dead on the Linux/headless deployment target.
- **Committed artifacts** `paris_sender.log` and `encryption.key` are not source and must leave the tree.

> Note: precise dead-code confirmation requires a reachability pass (e.g. `vulture`, coverage). Items above are candidates, not yet confirmed-unreachable, and must pass the Phase 12 dead-code gate before deletion.

---

## 5. Existing Tests (must be preserved)

`test_fixes.py` validates behaviors that the refactor must not regress:

- `TestTemplateValidation` — bracket placeholders, Jinja2 syntax, unclosed block/expression detection, variable extraction.
- `TestContextPreview` — autograb context derivation from email addresses (firstname/greetings/company).
- `TestDNSDomainChecker` — domain extraction + DNS result classification.
- `TestSentEmailLog` — log entry structure, severity classification, cap, filtering, CSV export.
- `TestURLSubstringSanitization` — exact-hostname matching for gmail/outlook URLs (anti-substring-injection).
- `TestParamikoHostKeyPolicy` — host-key policy presence, `urlparse` usage.
- `TestCollapsibleSection`, `TestGUIPerformance` — Tkinter-specific (will be retired/replaced with UI tests once Tkinter is removed).

**Autograb** logic (placeholders `[firstname]`/`[greetings]`/`[company]`, Jinja2 context, ISP-domain fallbacks) lives around lines 9434–9500 and is covered by `TestContextPreview`. Per the non-negotiable rules, this must be preserved and not rewritten absent a bug — extracted intact into a service in Phase 6/11.

---

## 6. Security Risks

| Severity | Finding | Location |
|----------|---------|----------|
| **Critical** | **`encryption.key` (Fernet key) committed to the repository.** Anyone with repo access can decrypt every stored SMTP/VPS password. The key must be rotated and purged from history (Phase 10). | `encryption.key`, used at `paris_sender_complete1.py:436,839–851` |
| High | Secrets handled as plaintext in memory and persisted via a local encrypted file fallback when keyring is unavailable; no managed secret store. | `IMAPHandler.password`, `AIHandler.api_key` (748), VPS `smtp_pass_encrypted` (928) |
| High | Committed `paris_sender.log` may leak recipient lists, server addresses, or operational data. | `paris_sender.log` |
| Medium | Paramiko host-key handling: tests assert a *warning* policy, not strict rejection — MITM exposure for SSH/proxy VPS connections. | `ProxyVPSHandler`, `TestParamikoHostKeyPolicy` |
| Medium | `allow_insecure_ssl` option and `_attempt_send(starttls_enabled)` permit downgraded/insecure TLS. | `SMTPHandler.configure_smtp` (2259), `universal_smtp_send` (118) |
| Medium | Browser automation (Selenium/undetected-chromedriver) and `subprocess` usage broaden the attack/supply-chain surface. | imports at 292–300 |
| Low | No startup security checks / secret scanning / config validation (Phase 10 deliverables absent). | n/a |

---

## 7. Dependency Review (`requirements.txt`)

- **Unpinned versions** — every dependency is floating; builds are not reproducible.
- **UI-era deps to retire** after migration: `tkcalendar` (Tkinter), and re-evaluate `matplotlib` (charts move to the Electron frontend).
- **Dual web stack:** both `flask`+`waitress` and `fastapi`+`uvicorn` present — consolidate on FastAPI/uvicorn.
- **Heavy automation stack:** `selenium`, `undetected-chromedriver`, `webdriver-manager` — confirm reachability before keeping.
- **Likely keepers** (backend): `aiosmtplib`, `jinja2`, `cryptography`, `dnspython`, `dkimpy`, `keyring`, `requests`, `user-agents`, `css-inline`, `PySocks`/`paramiko` (VPS), `PyPDF2`.
- **Action:** pin versions, split into `requirements/base|dev|optional`, drop UI-only packages in Phase 12.

---

## 8. Deliverability Review (current state)

Present today: `DeliverabilityHelper` (seed/link/DNS checks), link-health check (`check_link_health` 3330, `_run_link_health_check` 4017), a DNS checker tab, a deliverability tab, and a smart warmup with `warmup_schedule.json`.

Gaps vs. target: no first-class **deliverability score engine**, no **DKIM/SPF/DMARC generation**, no persisted **domain reputation/auth tracking**, no **inbox-placement** dashboard backed by a ledger. Warmup state is held in `tk.*Var`/JSON rather than a `WarmupService`.

---

## 9. Quality-Gate Baseline

- **Compiles:** `python -m py_compile paris_sender_complete1.py` — to be confirmed in CI; importing the module pulls heavy optional deps and Tkinter, so it is not import-safe in headless CI today.
- **Tests:** `python -m unittest test_fixes.py` is the existing suite; several tests construct Tkinter widgets and need a display/headless guard.
- **No linting / static analysis / dependency audit / dead-code scan** is configured in the repo today. These must be introduced (ruff/flake8, mypy, pip-audit, vulture) as part of the Phase quality gates.

---

## 10. Headline Findings (prioritized)

1. **Rotate & purge the committed `encryption.key` immediately** (Phase 10) — critical.
2. Remove committed runtime artifacts (`paris_sender.log`, key, future DB) and add `.gitignore`.
3. Establish the **UI → API → DeliveryService → Provider** seam to unwind UI/logic coupling (Phase 3).
4. Introduce the **transactional email ledger** schema — the foundation for analytics, deliverability, and health (Phase 5).
5. Consolidate the **4 duplicated MIME/send paths** behind one `DeliveryService` (Phase 3/12).
6. Preserve **autograb** and the existing `test_fixes.py` behaviors throughout.
7. Pin/split dependencies; consolidate on FastAPI; introduce lint/static/dep/dead-code gates.

See `migration_plan.md` for the phased execution roadmap.
