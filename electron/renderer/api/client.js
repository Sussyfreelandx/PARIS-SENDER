export const BASE_URL = window.parisAPI?.backendUrl || 'http://127.0.0.1:8000';

const listeners = new Set();

export function subscribeApiLogs(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function emitApiLog(entry) {
  const normalized = {
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
    timestamp: new Date().toISOString(),
    ...entry
  };
  listeners.forEach((listener) => listener(normalized));
}

function emit(level, action, details) {
  emitApiLog({ level, action, details });
}

function validateJsonPayload(payload) {
  if (payload === null || (typeof payload !== 'object' && !Array.isArray(payload))) {
    throw new Error('Invalid API JSON response');
  }
  return payload;
}

async function request(method, path, body) {
  const url = `${BASE_URL}${path}`;
  const options = {
    method,
    headers: {
      Accept: 'application/json'
    }
  };

  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  const startedAt = performance.now();
  try {
    emit('info', `${method} ${path}`, body ? 'request sent' : 'request sent without body');
    const response = await fetch(url, options);
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json') ? validateJsonPayload(await response.json()) : await response.text();

    if (!response.ok) {
      const message = typeof payload === 'string' ? payload : payload.detail || response.statusText;
      throw new Error(message);
    }

    emit('success', `${method} ${path}`, `completed in ${Math.round(performance.now() - startedAt)}ms`);
    return payload;
  } catch (error) {
    emit('error', `${method} ${path}`, error.message);
    throw error;
  }
}

export const get = (path) => request('GET', path);
export const post = (path, body) => request('POST', path, body);
export const patch = (path, body) => request('PATCH', path, body);
export const del = (path) => request('DELETE', path);

function queryString(params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, value);
  });
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const getHealth = () => get('/health');

/** Default per-attempt timeout for the startup/steady-state health probe. */
export const HEALTH_TIMEOUT_MS = 4000;

/**
 * Classify a low-level connection failure into an actionable category so the
 * UI can distinguish a backend boot delay from a crash or a network timeout
 * instead of collapsing everything into a single "offline" state.
 *
 * @param {unknown} error
 * @returns {{ kind: 'timeout'|'unreachable'|'crash'|'http'|'unknown', message: string }}
 */
export function classifyConnectionError(error) {
  if (!error) {
    return { kind: 'unknown', message: 'Unknown connection error.' };
  }
  // Pre-classified result bubbling up from probeHealth.
  if (typeof error === 'object' && error.kind) {
    return { kind: error.kind, message: error.message || String(error) };
  }
  if (error.name === 'AbortError') {
    return { kind: 'timeout', message: 'Backend did not respond in time (network timeout).' };
  }
  const message = error.message || String(error);
  // Browser fetch raises a TypeError for connection-refused, DNS, CORS and
  // offline conditions — i.e. the backend process is not reachable yet.
  if (error instanceof TypeError || /failed to fetch|networkerror|load failed|connection refused/i.test(message)) {
    return { kind: 'unreachable', message: message || 'Backend is unreachable.' };
  }
  return { kind: 'unknown', message };
}

/**
 * Probe the backend /health endpoint once with an explicit timeout. Never
 * throws: it always resolves to a structured result so the lifecycle layer can
 * branch on real, classified outcomes (boot delay vs crash vs timeout) without
 * fabricating a status or silently swallowing a failure.
 *
 * @param {object} [options]
 * @param {number} [options.timeoutMs=HEALTH_TIMEOUT_MS]
 * @returns {Promise<{ ok: boolean, status: string, version: string|null,
 *   payload?: object, httpStatus?: number,
 *   classification?: { kind: string, message: string } }>}
 */
export async function probeHealth({ timeoutMs = HEALTH_TIMEOUT_MS } = {}) {
  const url = `${BASE_URL}/health`;
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  const startedAt = performance.now();
  emit('info', 'GET /health', 'probe sent');
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: controller ? controller.signal : undefined
    });
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json') ? await response.json() : await response.text();

    if (!response.ok) {
      const detail = typeof payload === 'string' ? payload : payload?.detail || response.statusText;
      const kind = response.status >= 500 ? 'crash' : 'http';
      const message = `Backend returned HTTP ${response.status}: ${detail}`;
      emit('error', 'GET /health', message);
      return { ok: false, status: 'offline', httpStatus: response.status, classification: { kind, message } };
    }

    emit('success', 'GET /health', `completed in ${Math.round(performance.now() - startedAt)}ms`);
    const data = payload && typeof payload === 'object' ? payload : {};
    return { ok: true, status: data.status || 'ok', version: data.version || null, payload: data };
  } catch (error) {
    const classification = classifyConnectionError(error);
    emit('error', 'GET /health', classification.message);
    return { ok: false, status: 'offline', classification };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * Poll the backend /health endpoint until it responds or all attempts are
 * exhausted. This smooths over the brief window on startup where the backend
 * process is launching and the connection has not yet stabilized, so a single
 * transient "Failed to fetch" does not surface as a UI error.
 *
 * @param {object} [options]
 * @param {number} [options.attempts=30] Maximum number of attempts.
 * @param {number} [options.intervalMs=1000] Delay between attempts.
 * @param {(attempt: number, error: Error) => void} [options.onRetry]
 *   Optional callback invoked after each failed attempt (1-indexed).
 * @returns {Promise<object>} The successful /health payload.
 */
export async function getHealthWithRetry({ attempts = 30, intervalMs = 1000, onRetry } = {}) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await getHealth();
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        if (onRetry) onRetry(attempt, error);
        await new Promise((resolve) => setTimeout(resolve, intervalMs));
      }
    }
  }
  throw lastError || new Error('Backend health check failed');
}
export const getHealthStatus = () => get('/health/status');
export const getDiagnostics = () => get('/diagnostics');
export const getBackendVersion = () => get('/version');
export const getDomainHealth = (domain) => get(`/health/domain/${encodeURIComponent(domain)}`);
export const getServerHealth = (id) => get(`/health/server/${encodeURIComponent(id)}`);
export const getLogs = (params = {}) => get(`/logs${queryString(params)}`);
export const getLogSummary = () => get('/logs/summary');
export const createCampaign = (name) => post('/campaigns', { name });
export const listCampaigns = () => get('/campaigns');
export const getCampaign = (id) => get(`/campaigns/${encodeURIComponent(id)}`);
export const getCampaignMessages = (id) => get(`/campaigns/${encodeURIComponent(id)}/messages`);
export const deleteCampaign = (id) => del(`/campaigns/${encodeURIComponent(id)}`);
export const getCampaignScore = (id) => get(`/campaigns/${encodeURIComponent(id)}/score`);
export const predictCampaign = (id, payload) => post(`/campaigns/${encodeURIComponent(id)}/predict`, payload);
export const sendCampaign = (id, payload) => post(`/campaigns/${encodeURIComponent(id)}/send`, payload);
export const previewCompose = (payload) => post('/compose/preview', payload);
export const analyzeCompose = (payload) => post('/compose/analyze', payload);
export const testSmtp = (payload) => post('/smtp/test', payload);
export const getDomains = () => get('/domains');
export const createDomain = (payload) => post('/domains', payload);
export const getDomain = (id) => get(`/domains/${encodeURIComponent(id)}`);
export const verifyDomain = (id) => post(`/domains/${encodeURIComponent(id)}/verify`, {});
export const autoVerifyDomain = (id) => post(`/domains/${encodeURIComponent(id)}/auto-verify`, {});
export const diagnoseDomain = (id) => post(`/domains/${encodeURIComponent(id)}/diagnose`, {});
export const liveVerifyDomain = (id) => post(`/domains/${encodeURIComponent(id)}/verify/live`, {});
export const updateDmarcPolicy = (id, policy) => patch(`/domains/${encodeURIComponent(id)}/dmarc`, { policy });
export const rotateDkim = (id) => post(`/domains/${encodeURIComponent(id)}/dkim/rotate`, {});
export const deleteDomain = (id) => del(`/domains/${encodeURIComponent(id)}`);
export const getDomainHistory = (id) => get(`/domains/${encodeURIComponent(id)}/history`);
export const getWarmupDomains = () => get('/warmup/domains');
export const configureWarmup = (payload) => post('/warmup/domains', payload);
export const getWarmupStatus = (domain) => get(`/warmup/domains/${encodeURIComponent(domain)}/status`);
export const overrideWarmup = (domain, payload) => post(`/warmup/domains/${encodeURIComponent(domain)}/override`, payload);
