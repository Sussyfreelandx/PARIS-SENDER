# Phase 10 Security Hardening Log

## Changes made

- Added `backend/services/security.py` with `SecurityService` for Fernet encryption/decryption, MultiFernet-compatible key rotation, environment/keyring key loading, and generated fallback keys for tests.
- Updated `DomainRepository` to encrypt DKIM private keys before SQLite persistence and decrypt them on load. Legacy plaintext DKIM values remain readable.
- Added opt-in API hardening in `backend/api/security.py`:
  - HS256 JWT issue/validation helper (`issue_access_token`).
  - `create_app(enable_auth=False, enable_rate_limit=False, jwt_secret=None, rate_limit_requests=60, rate_limit_window_seconds=60)` remains backward compatible because auth and rate limiting default off.
  - Simple RBAC: `admin` can access all routes, `viewer` can read, `operator` can read and use campaign/compose/warmup write endpoints.
  - In-memory per-client/per-endpoint rate limiting.
- Added automatic sensitive context/message redaction in `LoggingService` for fields such as password, SMTP password, token, secret, API key, authorization, and DKIM private key.
- Added `.env.example` and allowed it through `.gitignore` while keeping real `.env*`, keys, logs, and DBs ignored.
- Added minimal renderer response validation in `electron/renderer/api/client.js` so JSON responses must be objects/arrays before use.
- Confirmed existing delivery controls are covered by tests: unverified managed sender domains are blocked, warmup limits are enforced, and deliverability thresholds gate sends.

## Tests added

- `tests/test_security_service.py`
- `tests/test_domain_encryption.py`
- `tests/test_api_security.py`
- `tests/test_logging_redaction.py`

## Validation results

- `python -m pytest tests/ -q` — **81 passed**, 1 Starlette/httpx deprecation warning.
- `python -m unittest test_fixes.py` — **216 passed**.
- `python -m pip install pip-audit -q && python -m pip_audit -r requirements.txt && python -m pip_audit -r requirements-dev.txt` — **No known vulnerabilities found** for both requirement files.
- `git ls-files | grep -iE '\.(key|pem|env|log)$'` — no tracked key/pem/env/log files found.
- High-confidence secret scan found only test assertions containing the literal text `BEGIN PRIVATE KEY`, not committed private key material.
