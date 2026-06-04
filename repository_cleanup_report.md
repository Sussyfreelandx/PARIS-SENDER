# Repository Cleanup Report

_Phase H audit. **Report only — nothing is deleted.** These are candidates for a
future, separately-reviewed cleanup PR._

## Method

* Searched `backend/` and `tests/` for usages of each runtime dependency.
* Looked for duplicate entrypoints, send/MIME implementations, and abandoned
  prototypes.
* Cross-checked `requirements.txt` against actual imports.

## Entrypoints (intentional, not duplicates)

These coexist by design; they are documented and each has a distinct role:

| File | Role |
| --- | --- |
| `backend/main.py` | **Canonical** freeze-safe launcher with crash logging (used by `backend.spec`). |
| `backend/server.py` | Builds the CORS-enabled `app`; exposes `main()` for `uvicorn backend.server:app`. |
| `packaging/backend_entry.py` | Thin frozen wrapper that delegates to `backend.main.run`. |
| `run_backend.py` | Simple fixed-port (`127.0.0.1:8000`) dev launcher. |

No action required; kept for clarity and backwards compatibility.

## Potentially unused runtime dependencies

The following are declared in `requirements.txt` but are **not imported anywhere
in `backend/` or `tests/`** (they are legacy of the Tkinter monolith / scraping
prototype). They are already `excludes`d in the PyInstaller spec, so they do not
bloat the executable, but they could be removed from `requirements.txt` after
confirming no out-of-tree script needs them:

* `selenium`
* `undetected-chromedriver`
* `webdriver-manager`
* `matplotlib`
* `tkcalendar`
* `pyngrok`
* `PyPDF2`
* `waitress`
* `flask`
* `paramiko`
* `PySocks`
* `user-agents`
* `css-inline`

> Caution: `keyring`, `cryptography`, `dnspython`, `dkimpy`, and `aiosmtplib` are
> used (security, domain/DKIM, delivery) and **must stay**.

## Duplicate implementations

* **Send / MIME**: `backend/services/delivery.py` (delivery orchestration),
  `backend/services/mime.py` (MIME construction), and `backend/services/health.py`
  (probe-only references) are **distinct, non-duplicated** responsibilities. No
  duplicate send pipeline was found in `backend/`.
* No duplicate startup scripts beyond the intentional entrypoints above.

## Documentation / logs

* 12 `PHASE*.md` logs plus `PROJECT_LOG.md`, `FINAL_SYSTEM_REPORT.md`,
  `audit_report.md`, `migration_plan.md`, `SECURITY_AUDIT.md`. These are
  historical phase records. Consider consolidating into `docs/` in a future
  housekeeping pass; not removed here to preserve project history.

## Recommended next steps (separate PR)

1. Trim the unused dependencies listed above from `requirements.txt` after a
   repo-wide grep confirms no external script imports them.
2. Move `PHASE*.md` into a `docs/history/` folder.
3. Add a `dev`/`prod` split to `requirements.txt` so build images stay lean.

No files were deleted as part of this task.
