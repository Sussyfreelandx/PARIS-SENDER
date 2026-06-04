# Phase 12 — Legacy Retirement and Non-SMTP Routing

## Changes made

- Added `NonSmtpDeliveryProvider` in `backend/services/delivery.py` using the shared `build_mime_message` seam and injectable transport callable.
- Exported the non-SMTP provider from `backend/services/__init__.py`.
- Updated `backend/api/app.py` so `non_smtp_delivery=true` selects the non-SMTP provider while preserving the same domain, deliverability, warmup, ledger, and logging flow.
- Added `delivery_channel` to send responses and campaign/delivery log contexts.
- Removed retired legacy files: `paris_sender_complete1.py` and `test_fixes.py`.
- Updated documentation to mark the Electron/FastAPI architecture as active.

## Tests added

- `tests/test_non_smtp_delivery.py` verifies provider selection, SMTP default routing, response channel metadata, deliverability gating before non-SMTP dispatch, and the non-SMTP MIME seam.

## Validation results

```bash
python -m pytest tests/ -q
```

Result: `105 passed, 1 skipped, 1 warning`.

## How to test

```bash
python -m pytest tests/ -q
```

Do not run the retired `test_fixes.py` suite; it was removed with the monolith.

## Files touched

- `backend/api/app.py`
- `backend/services/delivery.py`
- `backend/services/__init__.py`
- `backend/validators/compose.py`
- `tests/conftest.py`
- `tests/test_logging_api.py`
- `tests/test_warmup_api.py`
- `tests/test_non_smtp_delivery.py`
- `README.md`
- `migration_plan.md`
- `PROJECT_LOG.md`
- `PHASE12_AUDIT_REPORT.md`
- `FINAL_SYSTEM_REPORT.md`
- `PHASE12_LOG.md`
- Removed: `paris_sender_complete1.py`, `test_fixes.py`.