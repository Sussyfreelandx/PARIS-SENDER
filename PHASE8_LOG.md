# Phase 8 — Health Monitor — Implementation Log

Companion to `migration_plan.md`, `PROJECT_LOG.md`, and the prior phase logs.

## Objective

Implement an operator-facing health monitor across SMTP/MX/server probes, domain authentication, warmup throttling, ledger queue depth, throughput, and the non-SMTP delivery path while preserving the existing `/health` endpoint and send behavior.

## Polling and aggregation

`backend/models/health.py` adds:

- `HealthStatus` — traffic-light states: `green`, `yellow`, `red`, and `unknown`.
- `ComponentHealth` — common component status payload.
- `HealthServer` / `ServerHealth` — configured SMTP, MX, VPS, and proxy targets plus probe results.
- `QueueDepth` — queued/processing message and recipient counts.
- `DomainHealthSummary` — DKIM/SPF/DMARC score and alert details.

`backend/services/health.py` adds `HealthMonitorService`, with injectable `Clock` and deterministic probe seams. It does not perform real network calls by default: SMTP/MX/server probes return `unknown` unless tests or production wiring inject a checker. `SmtplibProbe` is available for explicit production SMTP EHLO/TLS probing.

The snapshot includes:

- Overall status as the worst component (`red` > `yellow` > `unknown` > `green`).
- Per-component status grid.
- Queue depth from read-only ledger status counts.
- Throughput from ledger events in the configured recent window.
- Domain alerts for DKIM/SPF/DMARC warning or critical states.
- VPS/proxy/SMTP/MX server grid.
- Warmup in-progress and throttled domain metrics.
- Non-SMTP delivery path metrics derived from queue and throughput, so the existing `non_smtp_delivery` send flag remains monitored without changing its behavior.

The service also provides `run_monitor(...)`, `start_health_monitor(...)`, and `stop_health_monitor(...)` helpers mirroring Phase 7 warmup polling. The async monitor is opt-in and caches the latest snapshot.

## API endpoints

`backend/api/app.py` keeps the original lightweight endpoint:

- `GET /health` → `{ "status": "ok" }`

New Phase 8 endpoints:

- `GET /health/status` — aggregate monitor snapshot.
- `GET /health/domain/{domain}` — detailed DKIM/SPF/DMARC health and required records, 404 when unknown.
- `GET /health/server/{server_id}` — detailed server probe metrics, 404 when unknown.

`create_app(...)` now accepts `health_service`, `health_servers`, and `enable_health_monitor=False`. Defaults are safe for existing callers and tests.

## Ledger helpers

`LedgerRepository` gained read-only helpers only; no schema changes were made:

- `status_counts(table="messages"|"recipients")`
- `event_status_counts_since(since)`

These support queue depth and throughput monitoring without altering autograb, campaign, message, recipient, or event persistence semantics.

## Electron UI panel

`electron/renderer/pages/HealthMonitor.jsx` adds a Health dashboard screen registered in `App.jsx` and `Sidebar.jsx`. Client helpers were added in `electron/renderer/api/client.js`:

- `getHealthStatus()`
- `getDomainHealth(domain)`
- `getServerHealth(id)`

The panel uses existing cards, tables, badges, notices, lists, metrics, and `HealthBars` styles. It shows component badges, queue depth, throughput, domain alerts, server grid, non-SMTP path metrics, and a configurable refresh interval.

### Textual UI sketch

```text
┌ Health monitor ─ overall badge ─ refresh interval ┐  ┌ Queue & throughput ┐
│ generated time, manual refresh                    │  │ active / sent / failed metrics │
└───────────────────────────────────────────────────┘  └────────────────────┘
┌ Components ─ green/yellow/red badges ─────────────┐  ┌ Domain alerts + bars ┐
│ queue, non-SMTP path, domains, warmup, servers    │  │ DKIM/SPF/DMARC state │
└───────────────────────────────────────────────────┘  └─────────────────────┘
┌ VPS / proxy / server grid ────────────────────────┐  ┌ Non-SMTP delivery path ┐
│ id | host | kind | badge | detail                 │  │ ledger throughput JSON │
└───────────────────────────────────────────────────┘  └───────────────────────┘
```

Real screenshots are unavailable in this headless session, so none were fabricated.

## Tests

Added:

- `tests/test_health.py` — fixed-clock unit tests for aggregation, server probe failures, unverified domain alerts, warmup throttling, queue depth, throughput, detail lookups, and async polling cache refresh.
- `tests/test_health_api.py` — FastAPI tests for `/health/status`, `/health/domain/{domain}`, `/health/server/{server_id}`, and 404 behavior using injected fakes.

Final validation:

```bash
python -m pytest tests/ -q
```

Result:

```text
64 passed, 1 warning in 5.57s
```

Renderer validation:

```bash
cd electron && npm install --quiet && npx vite build
```

Result:

```text
✓ built in 125ms
```

## Regression safety

| Gate item | Status |
|---|---|
| Original `GET /health` response preserved | ✅ |
| Health monitor defaults avoid real network probes | ✅ |
| Domain DKIM/SPF/DMARC health reuses `DomainService` | ✅ |
| Warmup monitoring delegates to `WarmupService` | ✅ |
| Ledger schema untouched; read-only helpers only | ✅ |
| Autograb compose paths untouched | ✅ |
| Existing campaign ledger flow untouched | ✅ |
| Non-SMTP send flag behavior untouched and now monitored | ✅ |
| API endpoints covered by TestClient tests | ✅ |
| Backend pytest suite remains green | ✅ |
| Electron Health panel builds with Vite | ✅ |
