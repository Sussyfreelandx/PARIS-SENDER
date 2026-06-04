# PARIS SENDER — DESKTOP APPLICATION

## 🚀 Overview

PARIS SENDER is a desktop email operations platform built with:

- **Electron** — Desktop UI
- **FastAPI** — Backend engine
- **DeliveryService** — SMTP + Non-SMTP providers
- **Ledger system** — event tracking
- **Autograb** — personalization engine
- **Deliverability** — scoring engine
- **Warmup** — system
- **Health monitoring** — system
- **Domain manager** — DKIM/SPF/DMARC

The desktop app bundles the FastAPI backend as an auto-started executable, so
end users never run Python or start a server manually.

## 🖥️ How to run (production mode)

### ✔ Windows

1. Download the installer: **`PARIS SENDER-Setup-<version>.exe`**
2. Double-click the installer and follow the prompts.
3. Launch **PARIS SENDER** from the Desktop or Start Menu shortcut.

The app will:

- Start the backend automatically
- Open the desktop UI
- Connect all services internally

### ✔ Mac

1. Open **`PARIS SENDER-<version>.dmg`**
2. Drag **PARIS SENDER** to **Applications**
3. Open the application.

The backend starts automatically inside the app.

## ⚙️ Requirements (developers only)

These are only required when running from source.

Install the Python backend dependencies:

```bash
pip install -r requirements.txt
```

Install the Node/Electron dependencies:

```bash
cd electron
npm install
```

Run development mode (starts the Vite renderer + Electron; the FastAPI backend
runs on `:8000`):

```bash
npm run dev
```

## 🚀 Production architecture

```
Electron UI (Desktop App)
        ↓
FastAPI Backend (auto-started executable)
        ↓
Services layer:
   - DeliveryService (SMTP / Non-SMTP)
   - Ledger
   - WarmupService
   - Deliverability Engine
   - Health Monitor
   - LoggingService
   - Domain Manager
```

## 📦 Building the application (developers only)

The Electron `dist` scripts build the backend executable, build the renderer,
and produce the platform installer in one step:

```bash
cd electron
npm run dist:win   # Windows .exe installer
npm run dist:mac   # macOS .dmg installer
```

To build only the backend executable directly with PyInstaller:

```bash
pip install -r requirements.txt -r packaging/requirements-build.txt
pyinstaller backend.spec --clean --noconfirm
```

`backend.spec` (repo root) is the canonical spec; it bundles the freeze-safe
`backend/main.py` launcher with the full hidden-import set.
`packaging/paris-backend.spec` remains as a shim that execs it.

The PyInstaller binary is staged into `electron/resources/backend/` by
`packaging/build_backend.py`, and electron-builder bundles it as an extra
resource. The electron-builder configuration lives in the `build` block of
`electron/package.json`.

Outputs (written to `electron/out/`):

- **Windows:** `PARIS SENDER-Setup-<version>.exe`
- **Mac:** `PARIS SENDER-<version>.dmg`

### Single-click startup model

Double-clicking the installed app triggers:

```
Double-click app → backend launches → /health passes → Electron window opens
```

The Electron main process (`electron/main/backend.js`) spawns the bundled
backend on a free loopback port and waits for `/health` before opening the
dashboard — no terminal, no Python install, and no manual launch steps.

### Startup logging / troubleshooting

If the backend ever fails to start, a full stack trace is written to
`logs/startup.log` (next to the executable in a packaged app, or in the repo
root when run from source). Startup failures are never swallowed silently.

## 🔐 Security notes

- No secrets are stored in the repo.
- Encryption keys are environment-based (`PARIS_SECRET_KEY` / `PARIS_SECRET_KEYS`)
  or sourced from the OS keyring.
- Logs are stored locally only.
- API authentication is available and intended to be enabled in production
  deployments.

## 🧪 Tests (developers only)

```bash
python -m pytest tests/ -q
```

## ❌ Legacy mode (removed)

The following are no longer supported:

- Running `python *.py` directly
- Tkinter UI execution
- Manual SMTP scripts
- Legacy CLI sender tools

## ✅ Final result

After installation:

- ✔ One-click launch
- ✔ Backend auto-start
- ✔ UI auto-connect
- ✔ Full system operational immediately
- ✔ No manual setup required
