import { useEffect, useMemo, useState } from 'react';
import { getHealthStatus } from '../api/client.js';
import Badge from '../components/Badge.jsx';
import HealthBars from '../components/HealthBars.jsx';

const toneFor = (status) => {
  if (status === 'green') return 'success';
  if (status === 'yellow') return 'warning';
  if (status === 'red') return 'danger';
  return 'neutral';
};

export default function HealthMonitor() {
  const [snapshot, setSnapshot] = useState(null);
  const [refreshSeconds, setRefreshSeconds] = useState(10);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const domainBars = useMemo(() => (snapshot?.domains || []).map((domain) => ({ label: domain.domain, value: domain.health_score })), [snapshot]);
  const criticalComponents = useMemo(() => (snapshot?.components || []).filter((component) => component.status === 'red'), [snapshot]);

  async function load() {
    setBusy(true);
    setError('');
    try {
      setSnapshot(await getHealthStatus());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { load(); }, []);
  useEffect(() => {
    const seconds = Math.max(1, Number(refreshSeconds) || 10);
    const timer = window.setInterval(load, seconds * 1000);
    return () => window.clearInterval(timer);
  }, [refreshSeconds]);

  const queue = snapshot?.queue_depth || {};
  const throughput = snapshot?.throughput || {};

  return (
    <div className="grid two">
      {error && <div className="notice danger">{error}</div>}
      {criticalComponents.length > 0 && <div className="notice danger">Critical failures: {criticalComponents.map((item) => item.name).join(', ')}</div>}

      <section className="card">
        <div className="card-header">
          <div>
            <p className="eyebrow">Overall</p>
            <h2>Health monitor</h2>
          </div>
          <Badge tone={toneFor(snapshot?.overall_status)}>{snapshot?.overall_status || 'loading'}</Badge>
        </div>
        <p className="muted">Generated {snapshot?.generated_at ? new Date(snapshot.generated_at).toLocaleString() : 'after first refresh'}.</p>
        <div className="form-row inline">
          <div><label>Refresh interval (seconds)</label><input type="number" min="1" value={refreshSeconds} onChange={(event) => setRefreshSeconds(event.target.value)} /></div>
          <div className="actions"><button className="ghost" disabled={busy} onClick={load} type="button">Refresh now</button></div>
        </div>
      </section>

      <section className="card">
        <h2>Queue & throughput</h2>
        <div className="grid three">
          <div><p className="metric">{queue.total ?? 0}</p><p className="muted">active depth</p></div>
          <div><p className="metric">{throughput.sent ?? 0}</p><p className="muted">sent in window</p></div>
          <div><p className="metric">{throughput.failed ?? 0}</p><p className="muted">failed in window</p></div>
        </div>
        <table className="table"><tbody>
          <tr><th>Queued messages</th><td>{queue.queued_messages ?? 0}</td></tr>
          <tr><th>Processing messages</th><td>{queue.processing_messages ?? 0}</td></tr>
          <tr><th>Queued recipients</th><td>{queue.queued_recipients ?? 0}</td></tr>
          <tr><th>Processing recipients</th><td>{queue.processing_recipients ?? 0}</td></tr>
        </tbody></table>
      </section>

      <section className="card">
        <h2>Components</h2>
        <div className="list">
          {(snapshot?.components || []).map((component) => (
            <div className="list-item" key={`${component.kind}-${component.name}`}>
              <div className="card-header">
                <div><strong>{component.name}</strong><span className="muted">{component.kind} · {component.detail}</span></div>
                <Badge tone={toneFor(component.status)}>{component.status}</Badge>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Domain alerts</h2>
        {domainBars.length > 0 && <HealthBars data={domainBars} />}
        <div className="list">
          {(snapshot?.domain_alerts || []).map((domain) => (
            <div className="list-item" key={domain.domain}>
              <div className="card-header"><strong>{domain.domain}</strong><Badge tone={toneFor(domain.status)}>{domain.status}</Badge></div>
              <p className="muted">{domain.detail}</p>
              <p className="muted">DKIM {String(domain.dkim_verified)} · SPF {String(domain.spf_verified)} · DMARC {String(domain.dmarc_verified)}</p>
            </div>
          ))}
          {(snapshot?.domain_alerts || []).length === 0 && <p className="muted">No domain authentication alerts.</p>}
        </div>
      </section>

      <section className="card">
        <h2>VPS / proxy / server grid</h2>
        <table className="table">
          <thead><tr><th>ID</th><th>Host</th><th>Kind</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>
            {(snapshot?.servers || []).map((server) => (
              <tr key={server.server_id}>
                <td><strong>{server.server_id}</strong></td>
                <td>{server.host}</td>
                <td>{server.kind}</td>
                <td><Badge tone={toneFor(server.status)}>{server.status}</Badge></td>
                <td>{server.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(snapshot?.servers || []).length === 0 && <p className="muted">No health servers configured.</p>}
      </section>

      <section className="card">
        <h2>Non-SMTP delivery path</h2>
        <p className="muted">The non-SMTP path is monitored through ledger queue depth and recent throughput, preserving the existing send flag behavior.</p>
        <pre className="code">{JSON.stringify(throughput.by_status || {}, null, 2)}</pre>
      </section>
    </div>
  );
}
