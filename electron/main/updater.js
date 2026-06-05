import electronUpdater from 'electron-updater';
import { app, ipcMain } from 'electron';
import fs from 'node:fs';
import path from 'node:path';

const { autoUpdater } = electronUpdater;

let initialized = false;
let getWindow = () => null;
let periodicTimer = null;

// Last known update status, replayed to any renderer that asks. This is the
// single source of truth surfaced by the diagnostics panel — it never contains
// fabricated values, only events emitted by electron-updater.
let lastStatus = { state: 'idle', updatedAt: new Date().toISOString() };

const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000; // every 6 hours during runtime

function logFilePath() {
  try {
    const dir = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(dir, { recursive: true });
    return path.join(dir, 'updater.log');
  } catch {
    return null;
  }
}

function diag(message, detail) {
  const line = `[${new Date().toISOString()}] ${message}${detail ? ` ${detail}` : ''}`;
  console.log(line);
  const file = logFilePath();
  if (file) {
    try {
      fs.appendFileSync(file, `${line}\n`);
    } catch {
      /* logging must never crash the updater */
    }
  }
}

function publish(state, extra = {}) {
  lastStatus = { state, updatedAt: new Date().toISOString(), ...extra };
  const win = getWindow();
  if (win && !win.isDestroyed()) {
    win.webContents.send('update:status', lastStatus);
  }
}

/**
 * Resolve the desired release channel.
 *
 * Channel can be selected with the ``PARIS_UPDATE_CHANNEL`` environment
 * variable (``stable`` | ``beta``). ``beta`` opts into pre-releases.
 */
function resolveChannel() {
  const channel = (process.env.PARIS_UPDATE_CHANNEL || 'stable').toLowerCase();
  return channel === 'beta' ? 'beta' : 'stable';
}

function applyChannel() {
  const channel = resolveChannel();
  autoUpdater.allowPrerelease = channel === 'beta';
  try {
    autoUpdater.channel = channel;
  } catch {
    /* some providers ignore channel; allowPrerelease still applies */
  }
  return channel;
}

async function runCheck(trigger) {
  diag(`Checking for updates (${trigger})`);
  publish('checking');
  try {
    await autoUpdater.checkForUpdates();
  } catch (error) {
    const message = error?.message || String(error);
    diag('Update check failed', message);
    publish('error', { error: message });
  }
}

/**
 * Wire up electron-updater for the packaged desktop app.
 *
 * Auto-update is only meaningful for installed/packaged builds, so this is a
 * no-op during development. The update feed is configured via the ``publish``
 * block in package.json (GitHub releases by default). Delta/differential
 * downloads are produced automatically by the NSIS target's blockmap.
 *
 * @param {() => (import('electron').BrowserWindow | null)} windowGetter
 */
export function initAutoUpdate(windowGetter) {
  if (typeof windowGetter === 'function') {
    getWindow = windowGetter;
  }
  registerIpc();
  if (initialized || !app.isPackaged) {
    return;
  }
  initialized = true;

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  const channel = applyChannel();
  diag(`Auto-update initialised on '${channel}' channel`);

  autoUpdater.on('error', (error) => {
    const message = error == null ? 'unknown' : error.message || String(error);
    diag('Auto-update error', message);
    publish('error', { error: message });
  });
  autoUpdater.on('checking-for-update', () => publish('checking'));
  autoUpdater.on('update-available', (info) => {
    diag('Update available', info?.version);
    publish('available', {
      version: info?.version ?? null,
      releaseNotes: normalizeNotes(info?.releaseNotes),
      releaseName: info?.releaseName ?? null,
      releaseDate: info?.releaseDate ?? null
    });
  });
  autoUpdater.on('update-not-available', () => publish('up-to-date'));
  autoUpdater.on('download-progress', (progress) => {
    publish('downloading', {
      percent: Math.round(progress?.percent ?? 0),
      bytesPerSecond: progress?.bytesPerSecond ?? 0,
      transferred: progress?.transferred ?? 0,
      total: progress?.total ?? 0
    });
  });
  autoUpdater.on('update-downloaded', (info) => {
    diag('Update downloaded', info?.version);
    publish('downloaded', {
      version: info?.version ?? null,
      releaseNotes: normalizeNotes(info?.releaseNotes),
      releaseName: info?.releaseName ?? null,
      releaseDate: info?.releaseDate ?? null
    });
  });

  // Check once on startup and then periodically while the app runs.
  runCheck('startup');
  periodicTimer = setInterval(() => runCheck('interval'), CHECK_INTERVAL_MS);
  if (periodicTimer.unref) {
    periodicTimer.unref();
  }
  app.on('before-quit', () => {
    if (periodicTimer) {
      clearInterval(periodicTimer);
      periodicTimer = null;
    }
  });
}

let ipcRegistered = false;
function registerIpc() {
  if (ipcRegistered) {
    return;
  }
  ipcRegistered = true;
  ipcMain.handle('update:get-status', () => lastStatus);
  ipcMain.handle('update:get-channel', () => resolveChannel());
  ipcMain.handle('update:check', async () => {
    if (!app.isPackaged) {
      return { state: 'unsupported', reason: 'not-packaged' };
    }
    await runCheck('manual');
    return lastStatus;
  });
  ipcMain.handle('update:install', () => {
    if (lastStatus.state !== 'downloaded') {
      return { ok: false, reason: 'no-update-downloaded' };
    }
    diag('Installing update and restarting');
    setImmediate(() => autoUpdater.quitAndInstall(false, true));
    return { ok: true };
  });
}

function normalizeNotes(notes) {
  if (!notes) {
    return null;
  }
  if (typeof notes === 'string') {
    return notes;
  }
  if (Array.isArray(notes)) {
    return notes
      .map((n) => (typeof n === 'string' ? n : `${n?.version ? `### ${n.version}\n` : ''}${n?.note ?? ''}`))
      .join('\n\n');
  }
  return null;
}
