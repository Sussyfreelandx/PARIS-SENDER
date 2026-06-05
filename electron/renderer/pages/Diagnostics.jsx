import { useCallback, useEffect, useState } from 'react';
import { getDiagnostics } from '../api/client.js';
import Badge from '../components/Badge.jsx';

const okTone = (ok) => (ok ? 'success' : 'danger');

/**
 * Centralized diagnostics panel (Part 5).
 *
 * Aggregates the real backend diagnostics (`/diagnostics`) with the live
 * auto-update status from the Electron main process. Every value shown is a
 * genuine probe result — there are no fabricated "healthy" placeholders.
 */
export default function Diagnostics() {
  const [diag, setDiag] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [update, setUpdate] = useState(null);
  const [channel, setChannel] = useState(null);

  const updates = typeof window !== 'undefined' ? window.parisAPI?.updates : null;

  const load = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      setDiag(await getDiagnostics());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!updates) return undefined;
    let active = true;
    updates.getStatus?.().then((s) => active && setUpdate(s)).catch(() => {});
    updates.getChannel?.().then((c) => active && setChannel(c)).catch(() => {});
    const unsubscribe = updates.onStatus?.((s) => active && setUpdate(s));
    return () => {
      active = false;
      if (typeof unsubscribe === 'function') unsubscribe();
    };
  }, [updates]);

  const appVersion = (typeof window !== 'undefined' && window.parisAPI?.appVersion) || 'unknown';
  const backendVersion = diag?.backend_version || 'unknown';
  const versionMismatch = backendVersion !== 'unknown' && appVersion !== 'unknown' && backendVersion !== appVersion;
  const dbOk = Boolean(diag?.database?.ok);
  const health = diag?.health || {};
  const lastError = diag?.last_error;

  async function checkForUpdates() {
    if (!updates) return;
    try {
      const result = await updates.check();
      setUpdate(result);
    } catch (err) {
      setUpdate({ state: 'error', error: err.message });
    }
  }

  return (
    <div className="grid two">
      {error && <div className="notice danger">Diagnostics unavailable: {error}</div>}

      <section className="card">
        <div className="card-header">
          <div>
            <p className="eyebrow">Versions</p>
            <h2>Application</h2>
          </div>
          <button className="ghost" disabled={busy} onClick={load} type="button">Refresh</button>
        </div>
        <ul className="diag-list">
          <li><span>App (frontend)</span><strong>v{appVersion}</strong></li>
          <li><span>Backend</span><strong>v{backendVersion}</strong></li>
          {versionMismatch && (
            <li><Badge tone="warning">Frontend / backend version mismatch</Badge></li>
          )}
        </ul>
      </section>

      <section className="card">
        <div className="card-header">
          <div>
            <p className="eyebrow">Services</p>
            <h2>Status</h2>
          </div>
        </div>
        <ul className="diag-list">
          <li><span>Database</span><Badge tone={okTone(dbOk)}>{dbOk ? 'connected' : 'error'}</Badge></li>
          {diag?.database?.error && <li className="muted">{diag.database.error}</li>}
          <li><span>Overall health</span><Badge tone={health.overall_status === 'green' ? 'success' : health.overall_status === 'yellow' ? 'warning' : health.overall_status === 'red' ? 'danger' : 'neutral'}>{health.overall_status || 'unknown'}</Badge></li>
        </ul>
        {Array.isArray(health.components) && health.components.length > 0 && (
          <ul className="diag-list">
            {health.components.map((c) => (
              <li key={c.name}><span>{c.name}</span><Badge tone={c.status === 'green' ? 'success' : c.status === 'yellow' ? 'warning' : 'danger'}>{c.status}</Badge></li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <div className="card-header">
          <div>
            <p className="eyebrow">Updates</p>
            <h2>Auto-update</h2>
          </div>
          {updates && <button className="ghost" onClick={checkForUpdates} type="button">Check now</button>}
        </div>
        {updates ? (
          <ul className="diag-list">
            <li><span>Channel</span><strong>{channel || 'stable'}</strong></li>
            <li><span>State</span><Badge tone={update?.state === 'error' ? 'danger' : update?.state === 'downloaded' || update?.state === 'up-to-date' ? 'success' : 'neutral'}>{update?.state || 'idle'}</Badge></li>
            {update?.version && <li><span>Version</span><strong>v{update.version}</strong></li>}
            {update?.state === 'downloading' && <li><span>Progress</span><strong>{update.percent ?? 0}%</strong></li>}
            {update?.state === 'error' && <li className="muted">{update.error}</li>}
          </ul>
        ) : (
          <p className="muted">Auto-update is only available in the packaged desktop build.</p>
        )}
      </section>

      <section className="card">
        <div className="card-header">
          <div>
            <p className="eyebrow">Last error</p>
            <h2>Diagnostics</h2>
          </div>
        </div>
        {lastError ? (
          <div className="stack">
            <Badge tone="danger">{lastError.severity || 'error'}</Badge>
            <p className="muted">{lastError.component ? `[${lastError.component}] ` : ''}{lastError.message}</p>
            {lastError.created_at && <p className="muted">{new Date(lastError.created_at).toLocaleString()}</p>}
          </div>
        ) : (
          <p className="muted">No errors recorded.</p>
        )}
      </section>
    </div>
  );
}
