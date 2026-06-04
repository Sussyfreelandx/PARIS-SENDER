# Phase 14 — Auto-update System

## Objective

Allow installed desktop builds to detect, download, and install new releases
automatically, so users stay current without re-downloading installers manually.

## Changes made

- Added `electron/main/updater.js`: initializes `electron-updater`'s
  `autoUpdater` for packaged builds only (`app.isPackaged`). It enables
  auto-download and install-on-quit, logs the full update lifecycle
  (checking / available / progress / downloaded / error), and calls
  `checkForUpdatesAndNotify()` on startup. It is a no-op in development.
- Wired `initAutoUpdate()` into `electron/main/main.js`, invoked after the
  backend is healthy in packaged builds.
- Added `electron-updater` to `electron/package.json` dependencies and a GitHub
  `publish` provider block (`LOBEG/PARIS-SENDER`) so electron-builder generates
  the update feed metadata (`latest.yml` / `latest-mac.yml`) during `dist`.

## Update feed

Releases are published to GitHub Releases. electron-builder uploads the
installer plus the update metadata when the build is run with a `GH_TOKEN`
environment variable (or via `electron-builder --publish`). Installed clients
then read the release feed on launch.

## Validation results

- `electron/main/updater.js` and `electron/main/main.js` pass `node --check`.
- Auto-update logic is gated behind `app.isPackaged`, so development runs and the
  existing test suite are unaffected (`105 passed, 1 skipped`).

## How to publish an update

```bash
cd electron
# bump "version" in package.json, then:
GH_TOKEN=<token> npm run dist:win   # or dist:mac
```

electron-builder publishes the installer and update metadata to the configured
GitHub repository's releases.
