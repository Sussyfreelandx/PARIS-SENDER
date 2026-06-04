import { app, BrowserWindow, shell, dialog } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startBackend, stopBackend } from './backend.js';
import { initAutoUpdate } from './updater.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const isDev = Boolean(process.env.VITE_DEV_SERVER_URL);

function createWindow() {
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1024,
    minHeight: 700,
    backgroundColor: '#0f172a',
    title: 'Paris Sender',
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
}

app.whenReady().then(async () => {
  // In development the backend is started separately (npm run dev assumes the
  // FastAPI server is already running on :8000). In the packaged app we launch
  // and supervise the bundled backend executable before opening the UI.
  if (!isDev) {
    try {
      await startBackend();
    } catch (error) {
      dialog.showErrorBox(
        'Paris Sender',
        `Failed to start the backend service.\n\n${error?.message || error}`
      );
      app.quit();
      return;
    }
    initAutoUpdate();
  }

  createWindow();

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
