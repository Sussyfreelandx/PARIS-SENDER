# Phase 4 — Domain Manager (DKIM/SPF/DMARC) — Implementation Log

Companion to `migration_plan.md` (Phase 4) and `PROJECT_LOG.md`.

## Objective

Implement full domain lifecycle management (backend + Electron UI): add/verify
domains, auto-generate DKIM keys, produce SPF/DMARC records, verify DNS, score
health over time, and enforce that campaigns only send from verified domains.

## What was built (backend)

- **`backend/models/domain.py`** — typed entities:
  - `Domain` (name, status, DKIM selector/private/public keys, SPF/DMARC records,
    DMARC policy, per-record verification flags, health score, timestamps,
    `to_dict()` for the API).
  - `DnsRecord` (record_type/host/value/verified/error) and `RecordType`,
    `DomainStatus` (PENDING/VERIFIED/FAILED) enums.
- **`backend/repositories/domain.py`** — `DomainRepository` (sqlite3,
  Postgres-ready, `:memory:` support, parameterized SQL):
  - `create/update/get/get_by_name/list/delete`.
  - `domain_health_history` table + `health_history()` — health score recorded
    over time on every create/update.
- **`backend/services/domain.py`** — `DomainService`:
  - `generate_dkim_keypair()` — RSA-2048 via `cryptography`; PKCS8 PEM private
    key + base64 SubjectPublicKeyInfo public key for the DNS TXT record.
  - `build_dkim_record / build_spf_record / build_dmarc_record` — generator
    functions producing correct host + value for each record type.
  - `add_domain()` — validates the name, rejects duplicates, generates keys and
    all three records, persists as PENDING.
  - `verify_domain()` — DNS verification via an injectable `DnsResolver` seam
    (`DnspythonResolver` in production, fakes in tests); updates per-record flags,
    overall status, `last_checked_at`, and health score.
  - `health_score()` — 0–100 weighted by verified records (DKIM 40 / SPF 30 /
    DMARC 30).
  - `regenerate_dkim()`, `update_dmarc_policy()`, `delete_domain()`,
    `is_domain_verified()`.
- **`backend/api/app.py`** — FastAPI endpoints:
  `GET/POST /domains`, `GET /domains/{id}`, `POST /domains/{id}/verify`,
  `PATCH /domains/{id}/dmarc`, `POST /domains/{id}/dkim/rotate`,
  `DELETE /domains/{id}`, `GET /domains/{id}/history`.
- **Campaign enforcement** — `POST /campaigns/{id}/send` now rejects (HTTP 400)
  any send whose sender domain is managed but not verified. Backward compatible:
  unmanaged sender domains are still allowed so existing behavior/tests are
  preserved; enforcement engages once a domain is onboarded.

## Non-SMTP / delivery integration

DKIM/SPF/DMARC records and the persisted DKIM private key are exposed through the
domain object so a non-SMTP delivery path can sign with the domain's key. The
delivery flow remains `UI → API → DeliveryService → Provider`; domain
verification gates which sender domains may be used.

## Tests

- `tests/test_domain.py` — 15 tests: name validation, key generation, record
  builders, add/duplicate/invalid, required records, verify (full/partial/none),
  health scoring, DKIM rotation, DMARC policy update, delete, health history,
  listing.
- `tests/test_domain_api.py` — domain lifecycle endpoints, verification via a
  fake resolver, and send blocked/allowed for unverified/verified/unmanaged
  domains.

## Quality Gate

| Gate item | Status |
|---|---|
| Domain add/edit/delete works | ✅ (service + API + tests) |
| DKIM/SPF/DMARC records generated correctly | ✅ (builders + tests) |
| Verification routines return correct status | ✅ (resolver seam + tests) |
| Campaigns cannot use unverified (managed) domains | ✅ (send enforcement + tests) |
| No regression in delivery logic | ✅ `python -m unittest test_fixes.py` = 216 pass / 1 skip |
| Domain repository + service tests pass | ✅ `python -m pytest tests/` = all green |

## Commands

```bash
python -m pytest tests/ -q              # backend suite (incl. domain + api + compose)
python -m unittest test_fixes.py        # legacy monolith parity suite
```

## Automation note

Periodic re-verification (cron / Electron background task) calls
`POST /domains/{id}/verify` on a schedule; the Domain Manager UI polls
`GET /domains` and `GET /domains/{id}/history` to reflect status and health in
real time.
