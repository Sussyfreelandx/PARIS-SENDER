# PHASE 11 — Full Test Pyramid Expansion

## Structure created

- `tests/unit/` — expanded service/repository unit coverage.
- `tests/integration/` — cross-service and API flow tests.
- `tests/e2e/` — API-level E2E flows plus optional Playwright smoke guard.
- `tests/performance/` — lightweight benchmark/throughput checks marked `performance`.
- `tests/fixtures/` — fixture documentation; executable shared fixtures live in `tests/conftest.py`.
- `tests/conftest.py` — shared in-memory repositories, fake delivery provider, fake DNS resolver, fixed clock, logging, warmup, health, deliverability, FastAPI app bundle, and TestClient fixtures.
- `pyproject.toml` — pytest discovery config, markers (`unit`, `integration`, `e2e`, `performance`, `slow`), and coverage source/report settings.

Existing flat `tests/test_*.py` files were kept in place so `python -m pytest tests/ -q` continues discovering all legacy pytest tests.

## Tests added

### Unit

`tests/unit/test_phase11_services.py` adds coverage for:

- DeliveryService non-SMTP success/failure batches and SMTP provider success/failure/quit behavior.
- Ledger status transitions for every `Status`, rollups, status counts, and invalid table handling.
- Warmup limit enforcement, blocked scheduling, and next-batch calculation.
- Deliverability weighted score correctness and verified-domain scoring.
- HealthMonitor queue/domain/warmup/probe status detection.
- LoggingService filtering, persistence, alert sink, nested redaction, message redaction, and summary.
- Domain DKIM/SPF/DMARC record generation and DNS validation mismatch paths with an injectable resolver.

### Integration/regression

`tests/integration/test_phase11_flows.py` adds:

- Campaign creation → send → fake provider → ledger rollup.
- Domain onboarding → DNS validation with fake resolver → verified state → managed-domain send allowed.
- Warmup enforcement blocking active send before provider dispatch.
- Deliverability score gate blocking the send endpoint.
- Cross-service logging assertions with end-to-end redaction.
- Regression coverage for Autograb personalization, ledger event shape, and delivery pipeline consistency.

### E2E

`tests/e2e/test_phase11_e2e.py` adds:

- API-level compose → analyze → campaign send → ledger event tracking flow.
- API-level domain create → DNS verify → campaign send allowed flow.
- Optional Playwright UI smoke test guarded by `pytest.importorskip("playwright")`; no browser downloads are required or triggered by default.

### Performance

`tests/performance/test_phase11_performance.py` adds fast, generous-threshold benchmarks for:

- Campaign bulk processing.
- Queue/ledger throughput.
- Logging throughput.
- DNS validation batch throughput.

## Dependencies

Updated `requirements-dev.txt`:

- Added `pytest-cov` for coverage commands.
- Added `pytest-xdist` for optional parallel runs.

Installed with:

```bash
python -m pip install -r requirements-dev.txt --quiet
```

## How to run

```bash
# Full pytest suite, including fast e2e/performance checks
python -m pytest tests/ -q

# By pyramid layer
python -m pytest tests/unit/ -q
python -m pytest tests/integration/ -q
python -m pytest tests/e2e/ -q
python -m pytest tests/performance/ -q

# Marker selection/deselection
python -m pytest tests/ -m unit -q
python -m pytest tests/ -m integration -q
python -m pytest tests/ -m e2e -q
python -m pytest tests/ -m performance -q
python -m pytest tests/ -m "not performance and not e2e" -q

# Coverage
python -m pytest tests/ --cov=backend --cov-report=term-missing -q
python -m pytest tests/ --cov=backend --cov-report=term-missing --cov-report=html -q

# Optional parallel execution
python -m pytest tests/ -n auto -q
```

## Validation results

- `python -m pytest tests/ -q` → `101 passed, 1 skipped`.
- Legacy monolith validation was retired in Phase 12 with `test_fixes.py`.
- `python -m pytest tests/ --cov=backend --cov-report=term-missing -q` → `101 passed, 1 skipped`, real total coverage `86%`.
- `python -m pytest tests/ --cov=backend --cov-report=term-missing --cov-report=html -q` → passed and wrote HTML coverage during validation.
- `python -m pytest tests/e2e/ -q` → `2 passed, 1 skipped`; skipped test is the optional Playwright smoke because Playwright is not installed.
- `python -m pytest tests/performance/ -q` → `4 passed`.

## Coverage gap vs 90% goal

Real coverage is `86%`, below the 90% target. Remaining gaps are concentrated in optional/background and defensive branches: FastAPI startup/shutdown scheduler hooks, auth/rate-limit edge cases, default dnspython resolver network path, SMTP production probe network path, logging/warmup async archiver/scheduler helpers, and some deliverability edge branches. No coverage numbers were fabricated.
