import { contextBridge, ipcRenderer } from 'electron';

const host = process.env.PARIS_HOST || '127.0.0.1';
const port = process.env.PARIS_PORT || '8000';
const backendUrl = `http://${host}:${port}`;

contextBridge.exposeInMainWorld('parisAPI', {
  backendUrl,
  appVersion: process.env.npm_package_version || '0.2.0',
  updates: {
    // Subscribe to live auto-update status events. Returns an unsubscribe fn.
    onStatus: (callback) => {
      if (typeof callback !== 'function') {
        return () => {};
      }
      const listener = (_event, status) => callback(status);
      ipcRenderer.on('update:status', listener);
      return () => ipcRenderer.removeListener('update:status', listener);
    },
    getStatus: () => ipcRenderer.invoke('update:get-status'),
    getChannel: () => ipcRenderer.invoke('update:get-channel'),
    check: () => ipcRenderer.invoke('update:check'),
    install: () => ipcRenderer.invoke('update:install')
  }
});
