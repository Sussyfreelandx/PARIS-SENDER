# PARIS SENDER Electron Frontend

PARIS SENDER uses an Electron shell and a Vite + React renderer.

## Prerequisites

- Node.js 20+
- Python with the backend requirements installed for development

## Install

```bash
cd electron
npm install
```

## Development

```bash
npm run dev
```

The dev script starts Vite on port 5173, waits for it, then launches Electron with hot reload from the Vite dev server. Electron also starts `../run_backend.py` on `http://127.0.0.1:8000` and waits for `/health` before opening the UI.

## Build

```bash
npm run build
```

This runs `vite build` and packages the app with `electron-builder`. Build output goes to `electron/out`.

## Distributable desktop app (Phases 13–14)

The packaged app bundles the FastAPI backend as a single executable and launches
it automatically — users do not need Python or a manually started backend.

```bash
# 1. Build the backend binary (requires Python deps + PyInstaller)
pip install -r ../requirements.txt -r ../packaging/requirements-build.txt
npm run build:backend

# 2. Build installers for the current platform (or a specific one)
npm run dist        # current platform
npm run dist:win    # Windows NSIS installer (.exe)
npm run dist:mac    # macOS disk image (.dmg)
```

`npm run dist` chains the backend build, renderer build, and electron-builder.
Installers are written to `electron/out/`. See `../packaging/README.md` for
details.

In packaged builds the Electron main process picks a free loopback port, starts
the bundled backend, waits for `/health`, and shuts it down on quit.
Auto-updates are handled by `electron-updater` against GitHub Releases (see
`../PHASE14_LOG.md`).

## Notes

All backend calls are centralized in `renderer/api/client.js` and target the FastAPI backend on port 8000 by default.
