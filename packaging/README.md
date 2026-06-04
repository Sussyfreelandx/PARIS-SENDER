# Paris Sender — Desktop Packaging

This directory contains the build tooling that turns the FastAPI backend into a
single, self-contained executable so the Electron desktop app can ship as a
Windows installer (`.exe`) or a macOS disk image (`.dmg`).

## Components

| File | Purpose |
| --- | --- |
| `backend_entry.py` | Frozen wrapper that bootstraps `sys.path` and delegates to `backend.main.run`. |
| `paris-backend.spec` | Backward-compat shim that execs the canonical `../backend.spec`. |
| `build_backend.py` | Runs PyInstaller (`backend.spec`) and stages the binary for electron-builder. |
| `requirements-build.txt` | Build-only dependencies (PyInstaller). |

> The canonical PyInstaller spec is `backend.spec` at the repository root. It
> targets `backend/main.py` — the freeze-safe launcher that writes startup
> failures to `logs/startup.log` so the packaged executable never exits silently.

## Build the backend binary

```bash
pip install -r requirements.txt -r packaging/requirements-build.txt
python packaging/build_backend.py
```

The binary is written to `dist/paris-backend` and copied to
`electron/resources/backend/` (gitignored) where electron-builder picks it up as
an `extraResources` entry.

## Build the full desktop app

From the `electron/` directory:

```bash
npm install
npm run dist        # current platform
npm run dist:win    # Windows NSIS installer (.exe)
npm run dist:mac    # macOS disk image (.dmg)
```

`npm run dist` runs the backend build, the Vite renderer build, and
electron-builder in sequence. Installers are written to `electron/out/`.

## Runtime model

* In **development** (`npm run dev`), the backend is expected to already be
  running on `:8000`; Electron loads the Vite dev server.
* In the **packaged app**, the Electron main process picks a free loopback port,
  launches the bundled `paris-backend` binary on it, waits for `/health`, then
  opens the UI. The backend is terminated when the app quits. The selected port
  is exported via `PARIS_PORT` and read by the preload script.

## Startup logging

`backend/main.py` wraps the whole startup sequence in a `try/except` and writes
any failure (import error, dependency error, path error, configuration error, or
hidden exception) with a full stack trace to `logs/startup.log` — located next to
the executable in a packaged app, or in the repo root when run from source. This
prevents the "launches briefly then exits with no visible error" failure mode.
