# Phase 7 — WarmupService — Implementation Log

Companion to `migration_plan.md`, `PROJECT_LOG.md`, and the prior phase logs.

## Objective

Implement domain/IP-style sender warmup controls alongside Phase 6 deliverability checks: configurable ramp-up limits, append-only scheduling/execution events, API enforcement before campaign delivery, Electron operator controls, and regression-safe SQLite storage outside the existing ledger schema.

## Config and limit model

`backend/models/warmup.py` adds:

- `WarmupConfig` — per-domain limits: `daily_limit`, `max_per_batch`, `max_per_hour`, `ramp_start_limit`, `ramp_days`, `enabled`, and `start_date`.
- `WarmupStatus` — UI progress: current ramp day, today's allowed limit, sent in rolling 24h/1h windows, remaining capacity, next batch time, and throttled flag.
- `WarmupEventType` — append-only event names.

Limits are enforced as the minimum remaining capacity across:

1. Ramped daily limit minus executed sends in the last 24 hours.
2. Hourly limit minus executed sends in the last hour.
3. `max_per_batch`.

Domains without a warmup config remain a no-op and preserve existing send behavior.

## Ramp-up schedule

Warmup starts at `ramp_start_limit` on day 1 and linearly increases through `ramp_days` until it reaches `daily_limit`. The service computes this dynamically from `start_date`, so persisted configs do not require a daily migration to remain current.

## Events

`backend/repositories/warmup.py` stores warmup data in separate tables:

- `warmup_domains` — config and ramp state per domain.
- `warmup_events` — append-only event log.

Event names:

- `WarmupScheduled`
- `WarmupExecuted`
- `WarmupOverride`

## API endpoints and send gate

`backend/api/app.py` now accepts `warmup_service: WarmupService | None = None` and wires a singleton dependency like deliverability.

New endpoints:

- `POST /warmup/domains` — enable/configure warmup.
- `GET /warmup/domains` — list enabled domains and configs.
- `GET /warmup/domains/{domain}/status` — return `WarmupStatus.to_dict()`.
- `POST /warmup/domains/{domain}/override` — local admin override; request body must include `authorized: true`.
- `GET /warmup/domains/{domain}/events` — recent append-only events.

Send integration order:

1. Campaign lookup.
2. Existing domain verification.
3. Existing Phase 6 deliverability score gate.
4. Warmup `check_send()` for enabled sender domains.
5. `WarmupScheduled`, delivery, then `WarmupExecuted`.

Warmup applies identically to SMTP and `non_smtp_delivery` sends.

## Electron UI panel

`electron/renderer/pages/Warmup.jsx` adds a Warmup screen registered in `App.jsx` and `Sidebar.jsx`. It supports:

- Listing warmup-enabled domains.
- Configuring daily/hourly/batch limits and ramp parameters.
- Viewing progress, remaining capacity, throttled state, and next batch time.
- Applying an admin override gated by an `authorized` checkbox.

Client helpers were added in `electron/renderer/api/client.js`.

Screenshots are unavailable in this headless CI/session environment because the Electron GUI cannot be launched with a display. No screenshots were fabricated.

## Automation scheduler

`WarmupService.run_ramp_scheduler()` is a lightweight async helper that periodically touches enabled configs. Ramp limits are computed from `start_date` on every request, so the scheduler is opt-in and non-blocking. `create_app(..., enable_warmup_scheduler=True)` registers startup/shutdown hooks; tests and default imports do not start it.

## Tests

Added:

- `tests/test_warmup.py` — deterministic fixed-clock unit tests for ramping, daily/hourly/batch blocking, rolling windows, scheduled/executed events, override behavior, and progress/next-batch values.
- `tests/test_warmup_api.py` — API configuration/status, send-limit enforcement, event logging, override authorization, and non-warmup regression sends.

Final validation:

```bash
python -m pytest tests/ -q
```

Result:

```text
58 passed, 1 warning in 7.61s
```

```bash
python -m unittest test_fixes.py
```

Result:

```text
Ran 216 tests in 0.627s

OK
```

```bash
cd electron && npm install --quiet && npx vite build
```

Result:

```text
✓ built in 126ms
```

## Phase 7 Quality Gate

| Gate item | Status |
|---|---|
| Warmup models with JSON-friendly `to_dict()` methods | ✅ |
| Separate warmup repository tables; ledger schema untouched | ✅ |
| Fixed-clock service for deterministic rolling-window tests | ✅ |
| Linear ramp-up schedule to full daily limit | ✅ |
| Daily, hourly, and max-per-batch enforcement | ✅ |
| Scheduled/executed/override events recorded | ✅ |
| Send endpoint enforces warmup after deliverability and before delivery | ✅ |
| Non-warmup domains remain no-op/regression-safe | ✅ |
| Warmup API endpoints implemented | ✅ |
| Electron Warmup panel registered and Vite build passes | ✅ |
| Opt-in async scheduler documented | ✅ |
| Backend pytest suite remains green | ✅ |
| Legacy unittest suite remains green | ✅ |
