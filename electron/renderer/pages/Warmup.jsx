import { useEffect, useMemo, useState } from 'react';
import { configureWarmup, getWarmupDomains, getWarmupStatus, overrideWarmup } from '../api/client.js';
import Badge from '../components/Badge.jsx';
import HealthBars from '../components/HealthBars.jsx';

const initialForm = {
  domain: '',
  daily_limit: 100,
  max_per_batch: 25,
  max_per_hour: 20,
  ramp_start_limit: 10,
  ramp_days: 7,
  enabled: true
};

export default function Warmup() {
  const [domains, setDomains] = useState([]);
  const [selected, setSelected] = useState('');
  const [status, setStatus] = useState(null);
  const [form, setForm] = useState(initialForm);
  const [override, setOverride] = useState({ authorized: false, daily_limit: '', max_per_batch: '', max_per_hour: '', bypass_remaining: false, detail: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const selectedConfig = useMemo(() => domains.find((item) => item.domain === selected)?.config, [domains, selected]);
  const bars = status ? [
    { label: 'today', value: status.sent_today, max: status.daily_limit },
    { label: 'hour', value: status.sent_this_hour, max: selectedConfig?.max_per_hour || status.sent_this_hour || 1 },
    { label: 'remaining', value: status.remaining_capacity, max: status.max_per_batch || 1 }
  ].map((item) => ({ ...item, value: item.max ? Math.round((item.value / item.max) * 100) : 0 })) : [];

  async function loadDomains() {
    const data = await getWarmupDomains();
    const next = data.domains || [];
    setDomains(next);
    setSelected((current) => current || next[0]?.domain || '');
  }

  async function loadStatus(domain = selected) {
    if (!domain) {
      setStatus(null);
      return;
    }
    setStatus(await getWarmupStatus(domain));
  }

  useEffect(() => { loadDomains().catch((err) => setError(err.message)); }, []);
  useEffect(() => { loadStatus(selected).catch((err) => selected && setError(err.message)); }, [selected]);

  async function withBusy(action) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveWarmup(event) {
    event.preventDefault();
    await withBusy(async () => {
      const payload = {
        ...form,
        daily_limit: Number(form.daily_limit),
        max_per_batch: Number(form.max_per_batch),
        max_per_hour: Number(form.max_per_hour),
        ramp_start_limit: Number(form.ramp_start_limit),
        ramp_days: Number(form.ramp_days)
      };
      const result = await configureWarmup(payload);
      setSelected(result.domain);
      setForm(initialForm);
      await loadDomains();
      await loadStatus(result.domain);
    });
  }

  async function applyOverride(event) {
    event.preventDefault();
    if (!selected) return;
    await withBusy(async () => {
      const payload = {
        authorized: override.authorized,
        bypass_remaining: override.bypass_remaining,
        detail: override.detail || undefined,
        daily_limit: override.daily_limit ? Number(override.daily_limit) : undefined,
        max_per_batch: override.max_per_batch ? Number(override.max_per_batch) : undefined,
        max_per_hour: override.max_per_hour ? Number(override.max_per_hour) : undefined
      };
      await overrideWarmup(selected, payload);
      await loadDomains();
      await loadStatus(selected);
    });
  }

  return (
    <div className="grid two">
      {error && <div className="notice danger">{error}</div>}
      <section className="card">
        <div className="card-header">
          <h2>Warmup domains</h2>
          <button className="ghost small" onClick={() => withBusy(loadDomains)} disabled={busy} type="button">Refresh</button>
        </div>
        <p className="muted">Enable a domain to enforce daily, hourly, and per-batch ramp-up limits before any SMTP or non-SMTP send.</p>
        <table className="table">
          <thead><tr><th>Domain</th><th>Daily</th><th>Hourly</th><th>Batch</th><th>Ramp</th></tr></thead>
          <tbody>
            {domains.map((item) => (
              <tr key={item.domain} onClick={() => setSelected(item.domain)} style={{ cursor: 'pointer' }}>
                <td><strong>{item.domain}</strong> {selected === item.domain && <Badge tone="success">selected</Badge>}</td>
                <td>{item.config.daily_limit}</td>
                <td>{item.config.max_per_hour}</td>
                <td>{item.config.max_per_batch}</td>
                <td>{item.config.ramp_start_limit} → {item.config.daily_limit} / {item.config.ramp_days}d</td>
              </tr>
            ))}
          </tbody>
        </table>
        {domains.length === 0 && <p className="muted">No warmup domains configured yet.</p>}
      </section>

      <section className="card">
        <h2>Enable warmup</h2>
        <form onSubmit={saveWarmup}>
          <div className="form-row"><label>Domain</label><input value={form.domain} onChange={(event) => setForm({ ...form, domain: event.target.value })} placeholder="example.com" required /></div>
          <div className="form-row inline">
            <div><label>Daily limit</label><input type="number" min="1" value={form.daily_limit} onChange={(event) => setForm({ ...form, daily_limit: event.target.value })} /></div>
            <div><label>Max per hour</label><input type="number" min="1" value={form.max_per_hour} onChange={(event) => setForm({ ...form, max_per_hour: event.target.value })} /></div>
            <div><label>Max per batch</label><input type="number" min="1" value={form.max_per_batch} onChange={(event) => setForm({ ...form, max_per_batch: event.target.value })} /></div>
          </div>
          <div className="form-row inline">
            <div><label>Ramp start</label><input type="number" min="1" value={form.ramp_start_limit} onChange={(event) => setForm({ ...form, ramp_start_limit: event.target.value })} /></div>
            <div><label>Ramp days</label><input type="number" min="1" value={form.ramp_days} onChange={(event) => setForm({ ...form, ramp_days: event.target.value })} /></div>
          </div>
          <div className="actions">
            <label className="switch"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> Enabled</label>
            <button className="primary" disabled={busy} type="submit">Save warmup</button>
          </div>
        </form>
      </section>

      <section className="card">
        <div className="card-header">
          <h2>Progress</h2>
          <button className="ghost small" onClick={() => withBusy(() => loadStatus())} disabled={!selected || busy} type="button">Refresh</button>
        </div>
        {status ? (
          <div className="list">
            <div className="card-header">
              <div><p className="eyebrow">{status.domain} · day {status.current_day}</p><p className="metric">{status.remaining_capacity}</p><p className="muted">sendable now</p></div>
              <Badge tone={status.throttled ? 'warning' : 'success'}>{status.throttled ? 'Throttled' : 'Ready'}</Badge>
            </div>
            <HealthBars data={bars} />
            <table className="table"><tbody>
              <tr><th>Today's limit</th><td>{status.daily_limit}</td></tr>
              <tr><th>Sent today</th><td>{status.sent_today}</td></tr>
              <tr><th>Sent this hour</th><td>{status.sent_this_hour}</td></tr>
              <tr><th>Remaining today</th><td>{status.remaining_today}</td></tr>
              <tr><th>Remaining this hour</th><td>{status.remaining_this_hour}</td></tr>
              <tr><th>Next batch</th><td>{status.next_batch_at ? new Date(status.next_batch_at).toLocaleString() : 'now'}</td></tr>
            </tbody></table>
          </div>
        ) : <p className="muted">Select a warmup domain to see progress.</p>}
      </section>

      <section className="card">
        <h2>Admin override</h2>
        <p className="muted">Local safety gate: check authorized before raising limits or bypassing remaining capacity.</p>
        <form onSubmit={applyOverride}>
          <div className="form-row inline">
            <div><label>Daily limit</label><input type="number" min="1" value={override.daily_limit} onChange={(event) => setOverride({ ...override, daily_limit: event.target.value })} placeholder={selectedConfig?.daily_limit || '100'} /></div>
            <div><label>Hourly limit</label><input type="number" min="1" value={override.max_per_hour} onChange={(event) => setOverride({ ...override, max_per_hour: event.target.value })} placeholder={selectedConfig?.max_per_hour || '20'} /></div>
            <div><label>Batch limit</label><input type="number" min="1" value={override.max_per_batch} onChange={(event) => setOverride({ ...override, max_per_batch: event.target.value })} placeholder={selectedConfig?.max_per_batch || '25'} /></div>
          </div>
          <div className="form-row"><label>Reason</label><input value={override.detail} onChange={(event) => setOverride({ ...override, detail: event.target.value })} placeholder="operator-approved one-time raise" /></div>
          <div className="actions">
            <label className="switch"><input type="checkbox" checked={override.authorized} onChange={(event) => setOverride({ ...override, authorized: event.target.checked })} /> authorized</label>
            <label className="switch"><input type="checkbox" checked={override.bypass_remaining} onChange={(event) => setOverride({ ...override, bypass_remaining: event.target.checked })} /> bypass remaining</label>
            <button className="secondary" disabled={!selected || busy} type="submit">Apply override</button>
          </div>
        </form>
      </section>
    </div>
  );
}
