# Startup Diagnostics — EXE Startup Failure Investigation

_Phase A root-cause analysis for the "packaged executable builds but immediately
closes" report._

## Summary

The packaged backend is launched from `packaging/backend_entry.py`, which (before
this change) did `from backend.server import main` **without ensuring the
repository root was on `sys.path`**. The backend also had **no startup crash
logging**, so any exception raised while importing the app or starting uvicorn
exited the process with nothing written to disk — exactly the "launches briefly,
exits immediately, no visible error" symptom.

The fixes are:

1. A freeze-safe, crash-logging entrypoint: `backend/main.py` (`run()` +
   `if __name__ == "__main__"`).
2. `packaging/backend_entry.py` now bootstraps `sys.path` and delegates to
   `backend.main.run`.
3. All startup failures are captured with a full stack trace in
   `logs/startup.log` — **no silent failures**.
4. A canonical, hardened PyInstaller spec at `backend.spec` with the full set of
   hidden imports.

A real PyInstaller build of `backend.spec` was produced and the resulting
executable was launched: it **starts, stays alive, and answers `GET /health`
with `200 {"status":"ok"}`**, writing `logs/startup.log` next to the binary.

## Investigation checklist

| # | Question | Finding |
|---|----------|---------|
| 1 | Does `backend/api/app.py` contain `if __name__ == "__main__"`? | No — and it does not need to. `app.py` defines `create_app()` and a module-level `app`. The launch entrypoint is `backend/server.py` / `backend/main.py`. |
| 2 | Does it launch uvicorn? | Yes — `backend/server.py:main()` and the new `backend/main.py:run()` call `uvicorn.run(app, ...)`. |
| 3 | Is uvicorn imported? | Yes (imported lazily inside `run()`/`main()` so failures are logged). |
| 4 | Is the FastAPI `app` object discoverable? | Yes — `backend.server:app` (built by `build_app()` → `create_app()` with CORS). |
| 5 | Are imports failing at runtime? | **Root cause.** Running `python packaging/backend_entry.py` raised `ModuleNotFoundError: No module named 'backend'` because the script's own directory (`packaging/`) — not the repo root — is placed on `sys.path[0]`. Reproduced and fixed. |
| 6 | Are relative imports broken after packaging? | The codebase uses absolute `backend.*` imports. PyInstaller bundles them via `collect_submodules("backend")`; the new `sys.path` bootstrap keeps source runs working too. |
| 7 | Are Jinja templates loading correctly? | No filesystem templates exist. `jinja2.Environment` is used purely in-memory (`backend/validators/autograb.py`, `compose.py`). No template-path resolution risk. |
| 8 | Are SQLite paths resolving correctly? | The desktop app builds the API with in-memory repositories (`:memory:`) by default, so there is no file-path dependency at startup. `sqlite3` is bundled as a hidden import. |
| 9 | Are environment variables missing? | `PARIS_HOST`/`PARIS_PORT` are optional with safe defaults (`127.0.0.1:8000`). Electron injects a free port. |
| 10 | Are hidden exceptions occurring before startup? | Previously any exception in `app = build_app()` (import time) propagated out and exited the process unlogged. Now wrapped and logged. |
| 11 | Are required folders missing from the bundle? | No `templates/static/config` folders are required. `logs/` is created at runtime by `backend/main.py`. |
| 12 | Is the EXE targeting the wrong file? | The spec targeted `backend_entry.py`; the canonical `backend.spec` now targets the robust `backend/main.py`. |
| 13 | Is Electron expecting a backend already running? | Only in dev (`npm run dev`). In the packaged app, `electron/main/backend.js` spawns the bundled binary and waits for `/health`. |
| 14 | Is the backend exiting immediately after initialization? | It was — due to (5). The frozen binary now stays alive (verified). |
| 15 | Are asyncio tasks terminating startup? | No. Background schedulers (warmup/health/log-archiver) are opt-in and disabled by default; they do not run in the desktop build. |
| 16 | Is logging configured before startup? | Now yes — `backend/main.py` writes to `logs/startup.log` before and during startup. |
| 17 | Is a startup exception being swallowed? | It was effectively invisible (printed to a console that closes instantly). Now persisted to `logs/startup.log`. |

## Identified root cause(s)

1. **Import-path fragility in the entrypoint** — `packaging/backend_entry.py` did
   not guarantee the repo root was importable when run as a script. Fixed via a
   `sys.path` bootstrap and the dedicated `backend/main.py` launcher.
2. **No startup crash logging** — startup exceptions vanished with no on-disk
   trace, matching the "no visible error" symptom. Fixed with `logs/startup.log`.
3. **Spec targeted a thin wrapper without the full hidden-import set** — the
   canonical `backend.spec` now targets `backend/main.py` and collects
   `fastapi/uvicorn/starlette/pydantic/anyio/jinja2/cryptography/email/dns/keyring`
   plus explicit uvicorn protocol/loop leaf modules, `sqlite3`, `bcrypt`,
   `dkim`, and `aiosmtplib`.

## Verification performed (Linux sandbox)

* `python packaging/backend_entry.py` — now starts; `/health` → `200`.
* `python -m backend.main` — starts; `/health` → `200`.
* Forced import failure — full traceback written to `logs/startup.log`, then
  re-raised (no silent failure).
* `pyinstaller backend.spec --clean --noconfirm` — build succeeds.
* Frozen binary launched — process **stays alive**, `/health` → `200`,
  `logs/startup.log` written next to the executable.
* `python -m pytest tests/` — 105 passed, 1 skipped.

> Windows `.exe` packaging, electron-builder installers, and the Electron desktop
> UI walkthrough (Phase G) require a Windows host with a display and are not
> runnable in this Linux CI sandbox. The cross-platform Python pipeline that
> drives them is validated above; see the final validation report for the exact
> commands to run on Windows.
