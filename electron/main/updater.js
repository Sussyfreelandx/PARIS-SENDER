import electronUpdater from 'electron-updater';
import { app } from 'electron';

const { autoUpdater } = electronUpdater;

let initialized = false;

/**
 * Wire up electron-updater for the packaged desktop app (Phase 14).
 *
 * Auto-update is only meaningful for installed/packaged builds, so this is a
 * no-op during development. The update feed is configured via the
 * ``publish`` block in package.json (GitHub releases by default).
 */
export function initAutoUpdate() {
  if (initialized || !app.isPackaged) {
    return;
  }
  initialized = true;

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on('error', (error) => {
    console.error('Auto-update error', error == null ? 'unknown' : error.message || error);
  });
  autoUpdater.on('checking-for-update', () => console.log('Checking for updates…'));
  autoUpdater.on('update-available', (info) => console.log(`Update available: ${info?.version ?? 'unknown'}`));
  autoUpdater.on('update-not-available', () => console.log('No updates available'));
  autoUpdater.on('download-progress', (progress) => {
    console.log(`Downloading update: ${Math.round(progress?.percent ?? 0)}%`);
  });
  autoUpdater.on('update-downloaded', (info) => {
    console.log(`Update downloaded: ${info?.version ?? 'unknown'} — will install on quit`);
  });

  autoUpdater.checkForUpdatesAndNotify().catch((error) => {
    console.error('Failed to check for updates', error?.message || error);
  });
}
