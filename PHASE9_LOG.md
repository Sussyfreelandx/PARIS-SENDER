# Phase 9 — LoggingService — Implementation Log

Companion to `migration_plan.md`, `PROJECT_LOG.md`, and the prior phase logs.

## Objective

Implement centralized backend logging across campaign sends, delivery summaries, deliverability scoring, warmup events, health snapshots, autograb previews, API querying, UI inspection, and opt-in archival while preserving existing ledger, autograb, and SMTP/non-SMTP send behavior.

## Centralized logging model and schema

`backend/models/logging.py` adds:

- `LogSeverity` — `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
- `LogComponent` — `API`, `AUTOGRAB`, `CAMPAIGN`, `DELIVERABILITY`, `DELIVERY`, `HEALTH`, and `WARMUP`.
- `LogEntry` — JSON-friendly structured entries with `id`, `timestamp`, `severity`, `component`, `message`, and analytics-ready `context`.

`backend/repositories/logging_repo.py` follows the warmup repository style: a single SQLite connection with `row_factory = sqlite3.Row`, `CREATE TABLE IF NOT EXISTS`, ISO text timestamps, and JSON context storage.

Schema:

```text
logs
├─ id INTEGER PRIMARY KEY AUTOINCREMENT
├─ timestamp TEXT NOT NULL
├─ severity TEXT NOT NULL
├─ component TEXT NOT NULL
├─ message TEXT NOT NULL
└─ context TEXT NOT NULL DEFAULT '{}'

indexes: component, severity, timestamp
```

The repository supports append, filtered newest-first query, summary analytics, live count, and deterministic archive/delete by cutoff.

## Service integration

`backend/services/logging_service.py` adds `LoggingService` with injectable `LogRepository`, injectable `Clock`, and optional `alert_sink`. Convenience helpers (`debug`, `info`, `warning`, `error`, `critical`) persist structured entries. `ERROR` and `CRITICAL` entries call the alert hook only when one is explicitly supplied.

Centralized emitters were integrated with optional/defaulted logger parameters so existing service constructors remain compatible:

- `DeliveryService` logs per-campaign delivery summaries with sent/failed counts.
- `DeliverabilityService` logs campaign and prospective score events.
- `WarmupService` logs domain configuration, scheduling, execution, and overrides.
- `HealthMonitorService` logs snapshot generation and degraded status.
- `send_campaign` logs campaign send start/result plus deliverability and warmup gate decisions, including the existing `non_smtp_delivery` flag.
- `/compose/preview` logs autograb personalization preview activity without changing `AutograbService` internals.

## API endpoints

`create_app(...)` now accepts `logging_service: LoggingService | None = None` and builds a safe in-memory singleton by default. It also accepts `enable_log_archiver: bool = False` for opt-in background archival.

New endpoints:

- `GET /logs` — filters by optional `severity`, `component`, `since`, `until`, and `limit`; returns `{ "logs": [...] }`.
- `GET /logs/summary` — returns total count, counts by severity/component, and earliest/latest timestamps.

## Electron UI panel

`electron/renderer/api/client.js` adds:

- `getLogs(params)`
- `getLogSummary()`

`electron/renderer/pages/ServerLogs.jsx` adds the Backend Logs panel and is registered in `App.jsx` and `Sidebar.jsx`. It includes filter dropdowns for severity/component, limit and refresh interval controls, manual refresh, a backend log table, expandable structured context, and client-side CSV/JSON exports using Blob downloads.

### Textual UI sketch

```text
┌ Server logs ─ total badge ───────────────────────────┐  ┌ Summary ┐
│ severity filter | component filter | limit | refresh │  │ counts by severity/component JSON │
│ Refresh now | Export CSV | Export JSON              │  │ latest timestamp                  │
└──────────────────────────────────────────────────────┘  └─────────┘
┌ Entries ───────────────────────────────────────────────────────────┐
│ time | severity badge | component | message | show/hide context   │
│ expanded rows show structured JSON context for analytics/debugging │
└────────────────────────────────────────────────────────────────────┘
```

Real screenshots are unavailable in this headless session, so none were fabricated.

## Automation and archiving

`LoggingService.run_archiver(...)`, `start_log_archiver(...)`, and `stop_log_archiver(...)` mirror the warmup/health async helper pattern. The archiver is disabled by default and deletes logs older than the configured max age when enabled.

## Tests

Added:

- `tests/test_logging.py` — fixed-clock repository/service tests for append, filters, timestamp ranges, summary counts, archive deletion, alert hooks, and JSON context round-trips.
- `tests/test_logging_api.py` — FastAPI tests for `/logs`, `/logs/summary`, campaign/delivery/deliverability/warmup send logging, non-SMTP context logging, autograb preview logging, and health snapshot logging.

Final validation:

```bash
python -m pytest tests/ -q
```

Result:

```text
72 passed, 1 warning in 6.06s
```

Renderer validation:

```bash
cd electron && npx vite build
```

Result:

```text
✓ built in 125ms
```

## Regression safety

| Gate item | Status |
|---|---|
| Existing service constructor positional args preserved | ✅ |
| Logging defaults are in-memory and side-effect-free | ✅ |
| Ledger schema and ledger persistence semantics untouched | ✅ |
| Autograb internals untouched; preview endpoint logs externally | ✅ |
| Non-SMTP send flag behavior untouched and now logged/monitored | ✅ |
| Warmup and deliverability gates still run before delivery | ✅ |
| Health monitor still avoids real network probes by default | ✅ |
| Backend `/logs` endpoints covered by TestClient tests | ✅ |
| Backend pytest suite remains green | ✅ |
| Electron renderer builds with Vite | ✅ |
