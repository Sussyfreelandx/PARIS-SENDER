# Security Audit

## Secrets posture

- No real key, PEM, env, or log files are tracked by Git.
- `.gitignore` excludes real secret/runtime artifacts (`*.key`, `.env`, `.env.*`, `*.log`, `*.db`) and explicitly permits `.env.example`.
- `.env.example` documents placeholders only: `PARIS_SECRET_KEY`, `PARIS_SECRET_KEYS`, `PARIS_JWT_SECRET`, and SMTP configuration placeholders.
- Repository secret scanning found no high-confidence committed credentials. Broad scans produce expected code/documentation references to password/token/DKIM handling.

## Encryption at rest

- DKIM private keys are now encrypted before persistence using `SecurityService` and Fernet tokens prefixed with `fernet:v1:`.
- `SecurityService` loads keys from `PARIS_SECRET_KEY`, `PARIS_SECRET_KEYS`, or OS keyring; tests and ephemeral local runs fall back to a generated key.
- Legacy plaintext DKIM rows remain readable for backward compatibility.

## API security

- Authentication and rate limiting are opt-in and default off, preserving existing `create_app()` behavior.
- JWTs use stdlib HMAC SHA-256 to avoid adding a new dependency.
- RBAC is intentionally simple and local: `admin`, `operator`, and `viewer` roles.
- `/health` remains unauthenticated when auth is enabled for liveness checks.

## Logging

- Structured log context and messages are redacted before persistence for common sensitive field names and inline secret-like labels.

## Electron

- Main-process hardening was already present (context isolation, no node integration, sandbox, navigation/window guards).
- Renderer API client now rejects malformed JSON payload shapes before returning data to the UI.

## Dependency audit

- `pip-audit` installed and ran successfully.
- `requirements.txt`: no known vulnerabilities found.
- `requirements-dev.txt`: no known vulnerabilities found.

## Residual risks

- In-memory API rate limiting is per-process and should be replaced with shared storage for multi-worker deployments.
- Fallback encryption keys are only safe for tests/ephemeral runs; production must set `PARIS_SECRET_KEY` or use a persistent OS keyring.
- JWT secret fallback is development-only; production must set `PARIS_JWT_SECRET`.
- Existing legacy plaintext DKIM rows are decrypted on read but not automatically migrated until the domain is updated/resaved.
