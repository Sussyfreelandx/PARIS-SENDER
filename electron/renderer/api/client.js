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
export const getDomainHealth = (domain) => get(`/health/domain/${encodeURIComponent(domain)}`);
export const getServerHealth = (id) => get(`/health/server/${encodeURIComponent(id)}`);
export const getLogs = (params = {}) => get(`/logs${queryString(params)}`);
export const getLogSummary = () => get('/logs/summary');
export const createCampaign = (name) => post('/campaigns', { name });
export const getCampaign = (id) => get(`/campaigns/${encodeURIComponent(id)}`);
export const getCampaignScore = (id) => get(`/campaigns/${encodeURIComponent(id)}/score`);
export const predictCampaign = (id, payload) => post(`/campaigns/${encodeURIComponent(id)}/predict`, payload);
export const sendCampaign = (id, payload) => post(`/campaigns/${encodeURIComponent(id)}/send`, payload);
export const previewCompose = (payload) => post('/compose/preview', payload);
export const analyzeCompose = (payload) => post('/compose/analyze', payload);
export const getDomains = () => get('/domains');
export const createDomain = (payload) => post('/domains', payload);
export const getDomain = (id) => get(`/domains/${encodeURIComponent(id)}`);
export const verifyDomain = (id) => post(`/domains/${encodeURIComponent(id)}/verify`, {});
export const updateDmarcPolicy = (id, policy) => patch(`/domains/${encodeURIComponent(id)}/dmarc`, { policy });
export const rotateDkim = (id) => post(`/domains/${encodeURIComponent(id)}/dkim/rotate`, {});
export const deleteDomain = (id) => del(`/domains/${encodeURIComponent(id)}`);
export const getDomainHistory = (id) => get(`/domains/${encodeURIComponent(id)}/history`);
export const getWarmupDomains = () => get('/warmup/domains');
export const configureWarmup = (payload) => post('/warmup/domains', payload);
export const getWarmupStatus = (domain) => get(`/warmup/domains/${encodeURIComponent(domain)}/status`);
export const overrideWarmup = (domain, payload) => post(`/warmup/domains/${encodeURIComponent(domain)}/override`, payload);
