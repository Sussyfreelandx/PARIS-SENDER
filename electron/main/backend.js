import { spawn } from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { app } from 'electron';

const HOST = '127.0.0.1';
const DEV_PORT = 8000;
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '..', '..');

let backendProcess = null;

/** Find a free TCP port on the loopback interface. */
function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, HOST, () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** Resolve the bundled backend executable inside the packaged app resources. */
function resolveBackendBinary() {
  const exeName = process.platform === 'win32' ? 'paris-backend.exe' : 'paris-backend';
  return path.join(process.resourcesPath, 'backend', exeName);
}

function resolveDevBackendScript() {
  return path.join(repoRoot, 'run_backend.py');
}

/** Poll the backend /health endpoint until it responds or the timeout elapses. */
function waitForHealth(port, { timeoutMs = 30000, intervalMs = 300 } = {}) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get({ host: HOST, port, path: '/health', timeout: 2000 }, (res) => {
        res.resume();
        if (res.statusCode === 200) {
          resolve();
        } else {
          retry();
        }
      });
      req.on('error', retry);
      req.on('timeout', () => {
        req.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error('Backend did not become healthy in time'));
        return;
      }
      setTimeout(attempt, intervalMs);
    };
    attempt();
  });
}

/**
 * Start the bundled FastAPI backend as a child process and wait until it is
 * ready. Returns the chosen port. Exported env (PARIS_PORT) lets the preload
 * script point the renderer at the correct backend URL.
 */
export async function startBackend() {
  const isDev = Boolean(process.env.VITE_DEV_SERVER_URL);
  const port = isDev ? DEV_PORT : await findFreePort();
  process.env.PARIS_HOST = HOST;
  process.env.PARIS_PORT = String(port);

  let command;
  let args;
  let cwd;

  if (isDev) {
    const script = resolveDevBackendScript();
    if (!fs.existsSync(script)) {
      throw new Error(`Backend entrypoint not found at ${script}`);
    }
    command = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
    args = [script];
    cwd = repoRoot;
  } else {
    const binary = resolveBackendBinary();
    if (!fs.existsSync(binary)) {
      throw new Error(`Backend executable not found at ${binary}`);
    }
    command = binary;
    args = [];
  }

  backendProcess = spawn(command, args, {
    cwd,
    env: { ...process.env, PARIS_HOST: HOST, PARIS_PORT: String(port) },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  });

  backendProcess.stdout?.on('data', (chunk) => process.stdout.write(`[backend] ${chunk}`));
  backendProcess.stderr?.on('data', (chunk) => process.stderr.write(`[backend] ${chunk}`));
  backendProcess.on('error', (error) => {
    backendProcess = null;
    console.error('Backend failed to start', error);
  });
  backendProcess.on('exit', (code, signal) => {
    backendProcess = null;
    if (code && code !== 0) {
      console.error(`Backend exited unexpectedly (code=${code}, signal=${signal})`);
    }
  });

  await waitForHealth(port);
  return port;
}

/** Terminate the backend child process if it is still running. */
export function stopBackend() {
  if (!backendProcess) {
    return;
  }
  const child = backendProcess;
  backendProcess = null;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(child.pid), '/f', '/t']);
    } else {
      child.kill('SIGTERM');
    }
  } catch (error) {
    console.error('Failed to stop backend process', error);
  }
}

// Best-effort cleanup so a crashing/exiting main process never orphans the
// backend child.
app.on('before-quit', stopBackend);
app.on('will-quit', stopBackend);
process.on('exit', stopBackend);
