import { contextBridge } from 'electron';

const host = process.env.PARIS_HOST || '127.0.0.1';
const port = process.env.PARIS_PORT || '8000';
const backendUrl = `http://${host}:${port}`;

contextBridge.exposeInMainWorld('parisAPI', {
  backendUrl,
  appVersion: process.env.npm_package_version || '0.2.0'
});
