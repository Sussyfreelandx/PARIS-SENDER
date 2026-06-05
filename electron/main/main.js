import { app, BrowserWindow, shell, dialog } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startBackend, stopBackend } from './backend.js';
import { initAutoUpdate } from './updater.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const isDev = Boolean(process.env.VITE_DEV_SERVER_URL);

let mainWindowRef = null;

function createWindow() {
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1024,
    minHeight: 700,
    backgroundColor: '#0f172a',
    title: 'PARIS SENDER',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: isDev
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://') || url.startsWith('http://')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowedDevUrl = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173';
    const allowedProdUrl = new URL(`file://${path.join(__dirname, '../dist/index.html')}`).toString();
    let target;
    try {
      target = new URL(url);
    } catch {
      event.preventDefault();
      return;
    }
    const allowedOrigins = new Set();
    try { allowedOrigins.add(new URL(allowedDevUrl).origin); } catch { /* ignore */ }
    const isDevTarget = (target.protocol === 'http:' || target.protocol === 'https:') && allowedOrigins.has(target.origin);
    const isProdTarget = target.protocol === 'file:' && url === allowedProdUrl;
    if (!isDevTarget && !isProdTarget) {
      event.preventDefault();
    }
  });

  if (isDev) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
  }

  mainWindowRef = mainWindow;
  mainWindow.on('closed', () => {
    if (mainWindowRef === mainWindow) {
      mainWindowRef = null;
    }
  });
}

app.whenReady().then(async () => {
  try {
    await startBackend();
  } catch (error) {
    dialog.showErrorBox(
      'PARIS SENDER',
      `Failed to start the backend service.\n\n${error?.message || error}`
    );
    app.quit();
    return;
  }

  createWindow();

  // Always wire IPC (renderer can query status/channel); the real updater only
  // activates for packaged builds.
  initAutoUpdate(() => mainWindowRef);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
