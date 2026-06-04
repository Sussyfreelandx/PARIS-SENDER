# Phase 13 — Desktop EXE / Mac DMG Packaging

## Objective

Build a single distributable desktop application that runs all required
components — the Electron UI plus the FastAPI backend, services layer, autograb,
ledger, delivery, and HTML/plain-text message support — without requiring the
user to start the Python backend manually.

## Changes made

- Added `backend/server.py`: a uvicorn entrypoint that builds the FastAPI app
  via `create_app()`, enables CORS for the local Electron origins (`null`,
  localhost dev/prod), and reads `PARIS_HOST` / `PARIS_PORT` from the
  environment (defaults `127.0.0.1:8000`). Runnable via `python -m backend.server`.
- Added PyInstaller bundling under `packaging/`:
  - `packaging/backend_entry.py` — frozen entrypoint wrapping `backend.server.main`.
  - `packaging/paris-backend.spec` — produces a single `paris-backend` executable,
    collecting uvicorn/fastapi/backend submodules and excluding heavy unused
    libraries (tkinter, matplotlib, selenium, undetected-chromedriver).
  - `packaging/build_backend.py` — runs PyInstaller and stages the binary to
    `electron/resources/backend/`.
  - `packaging/requirements-build.txt` — build-only dependency (PyInstaller).
- Wired the backend into the Electron main process:
  - `electron/main/backend.js` — picks a free loopback port, spawns the bundled
    backend binary, polls `/health` until ready, and terminates the child on quit.
  - `electron/main/main.js` — starts/supervises the backend in packaged builds
    (dev mode unchanged), shows an error dialog if startup fails.
  - `electron/main/preload.js` — derives `backendUrl` from `PARIS_HOST`/`PARIS_PORT`
    so the renderer targets the dynamically selected port.
- Updated `electron/package.json` electron-builder config:
  - Windows `nsis` target (installer `.exe`), macOS `dmg` target, Linux AppImage.
  - `extraResources` copies the staged backend binary into the app's `resources/backend`.
  - Added `build:renderer`, `build:backend`, `dist`, `dist:win`, `dist:mac` scripts.
- Added `electron/resources/backend` to `electron/.gitignore` (built artifact).

## Validation results

- `python -m backend.server` and the **frozen** `paris-backend` binary both serve
  `GET /health` → `{"status":"ok"}` on the configured port, with the CORS header
  present for the `null` origin.
- `python packaging/build_backend.py` produces and stages the executable successfully.
- Electron main/preload/backend/updater JS pass `node --check`.
- Existing backend suite unchanged: `python -m pytest tests/ -q` → `105 passed, 1 skipped`.

## How to build

```bash
pip install -r requirements.txt -r packaging/requirements-build.txt
cd electron && npm install && npm run dist   # or dist:win / dist:mac
```

Installers are written to `electron/out/`.
