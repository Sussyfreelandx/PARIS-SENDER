import { useMemo, useState } from 'react';
import Badge from '../components/Badge.jsx';
import { useLogs } from '../components/LogContext.jsx';

export default function Logs() {
  const { logs, clearLogs } = useLogs();
  const [filter, setFilter] = useState('all');
  const filtered = useMemo(() => filter === 'all' ? logs : logs.filter((log) => log.level === filter), [filter, logs]);

  return (
    <section className="card">
      <div className="card-header">
        <h2>Client activity log</h2>
        <div className="actions">
          <select value={filter} onChange={(event) => setFilter(event.target.value)}>
            <option value="all">All severities</option>
            <option value="info">Info</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
          </select>
          <button className="ghost small" onClick={clearLogs} type="button">Clear</button>
        </div>
      </div>
      <div className="list">
        {filtered.map((log) => (
          <div className="list-item log-row" key={log.id || `${log.timestamp}-${log.action}`}>
            <span className="muted">{new Date(log.timestamp).toLocaleString()}</span>
            <Badge tone={log.level === 'error' ? 'danger' : log.level === 'success' ? 'success' : 'neutral'}>{log.level}</Badge>
            <div><strong>{log.action}</strong><p className="muted">{log.details}</p></div>
          </div>
        ))}
        {filtered.length === 0 && <p className="muted">No log entries captured yet.</p>}
      </div>
    </section>
  );
}
