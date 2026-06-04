import { useEffect, useMemo, useState } from 'react';
import { getLogs, getLogSummary } from '../api/client.js';
import Badge from '../components/Badge.jsx';

const severities = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
const components = ['', 'API', 'AUTOGRAB', 'CAMPAIGN', 'DELIVERABILITY', 'DELIVERY', 'HEALTH', 'WARMUP'];

const toneFor = (severity) => {
  if (severity === 'CRITICAL' || severity === 'ERROR') return 'danger';
  if (severity === 'WARNING') return 'warning';
  if (severity === 'INFO') return 'success';
  return 'neutral';
};

function download(name, type, content) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function csvValue(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '');
  return `"${text.replaceAll('"', '""')}"`;
}

export default function ServerLogs() {
  const [logs, setLogs] = useState([]);
  const [summary, setSummary] = useState(null);
  const [filters, setFilters] = useState({ severity: '', component: '', limit: 100 });
  const [refreshSeconds, setRefreshSeconds] = useState(5);
  const [expanded, setExpanded] = useState({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const activeFilters = useMemo(() => ({ ...filters, limit: Number(filters.limit) || 100 }), [filters]);

  async function load() {
    setBusy(true);
    setError('');
    try {
      const [logData, summaryData] = await Promise.all([getLogs(activeFilters), getLogSummary()]);
      setLogs(logData.logs || []);
      setSummary(summaryData);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { load(); }, [activeFilters]);
  useEffect(() => {
    const seconds = Math.max(1, Number(refreshSeconds) || 5);
    const timer = window.setInterval(load, seconds * 1000);
    return () => window.clearInterval(timer);
  }, [refreshSeconds, activeFilters]);

  function exportJson() {
    download('backend-logs.json', 'application/json', JSON.stringify(logs, null, 2));
  }

  function exportCsv() {
    const rows = [['timestamp', 'severity', 'component', 'message', 'context'], ...logs.map((log) => [
      log.timestamp,
      log.severity,
      log.component,
      log.message,
      log.context
    ])];
    download('backend-logs.csv', 'text/csv', rows.map((row) => row.map(csvValue).join(',')).join('\n'));
  }

  return (
    <div className="grid two">
      {error && <div className="notice danger">{error}</div>}

      <section className="card">
        <div className="card-header">
          <div>
            <p className="eyebrow">Centralized backend</p>
            <h2>Server logs</h2>
          </div>
          <Badge tone="neutral">{summary?.total ?? logs.length} total</Badge>
        </div>
        <div className="form-row inline">
          <div><label>Severity</label><select value={filters.severity} onChange={(event) => setFilters({ ...filters, severity: event.target.value })}>{severities.map((item) => <option key={item || 'all'} value={item}>{item || 'All'}</option>)}</select></div>
          <div><label>Component</label><select value={filters.component} onChange={(event) => setFilters({ ...filters, component: event.target.value })}>{components.map((item) => <option key={item || 'all'} value={item}>{item || 'All'}</option>)}</select></div>
          <div><label>Limit</label><input type="number" min="1" value={filters.limit} onChange={(event) => setFilters({ ...filters, limit: event.target.value })} /></div>
          <div><label>Refresh interval (seconds)</label><input type="number" min="1" value={refreshSeconds} onChange={(event) => setRefreshSeconds(event.target.value)} /></div>
        </div>
        <div className="actions">
          <button className="ghost small" onClick={load} disabled={busy} type="button">Refresh now</button>
          <button className="ghost small" onClick={exportCsv} disabled={!logs.length} type="button">Export CSV</button>
          <button className="ghost small" onClick={exportJson} disabled={!logs.length} type="button">Export JSON</button>
        </div>
      </section>

      <section className="card">
        <h2>Summary</h2>
        <div className="grid two">
          <div><p className="metric">{summary?.total ?? 0}</p><p className="muted">stored entries</p></div>
          <div><p className="muted">Latest</p><strong>{summary?.latest_timestamp ? new Date(summary.latest_timestamp).toLocaleString() : 'none'}</strong></div>
        </div>
        <pre className="code">{JSON.stringify({ severity: summary?.by_severity || {}, component: summary?.by_component || {} }, null, 2)}</pre>
      </section>

      <section className="card" style={{ gridColumn: '1 / -1' }}>
        <h2>Entries</h2>
        <table className="table">
          <thead><tr><th>Time</th><th>Severity</th><th>Component</th><th>Message</th><th>Context</th></tr></thead>
          <tbody>
            {logs.map((log) => (
              <tr className="log-row" key={log.id}>
                <td>{new Date(log.timestamp).toLocaleString()}</td>
                <td><Badge tone={toneFor(log.severity)}>{log.severity}</Badge></td>
                <td>{log.component}</td>
                <td>{log.message}</td>
                <td>
                  <button className="ghost small" type="button" onClick={() => setExpanded({ ...expanded, [log.id]: !expanded[log.id] })}>{expanded[log.id] ? 'Hide' : 'Show'}</button>
                  {expanded[log.id] && <pre className="code">{JSON.stringify(log.context || {}, null, 2)}</pre>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {logs.length === 0 && <p className="muted">No backend logs match the current filters.</p>}
      </section>
    </div>
  );
}
