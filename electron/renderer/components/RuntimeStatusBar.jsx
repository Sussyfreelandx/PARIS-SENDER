import Badge from './Badge.jsx';
import { connectionLabel, connectionTone, snapshotTone } from '../lib/runtimeStatus.js';

/**
 * Compact, always-available runtime status surface.
 *
 * Renders the central runtime status object (backend / update / database /
 * delivery) as a row of badges, with an expandable diagnostics list that
 * surfaces any active failure. Nothing here is fabricated: each value reflects
 * a real probe result, and "unknown" is shown when a probe has not produced a
 * value yet rather than an optimistic placeholder.
 *
 * @param {{ runtime: {
 *   backend: object,
 *   update: object,
 *   database: { ok: boolean|null, error: string|null },
 *   delivery: { status: string, detail: string|null }
 * } }} props
 */
export default function RuntimeStatusBar({ runtime }) {
  const { backend, update, database, delivery } = runtime;

  const databaseTone = database.ok === null ? 'neutral' : database.ok ? 'success' : 'danger';
  const databaseLabel = database.ok === null ? 'unknown' : database.ok ? 'connected' : 'error';

  const updateTone = update.error
    ? 'danger'
    : update.update_available
      ? 'warning'
      : update.state === 'up-to-date'
        ? 'success'
        : 'neutral';
  const updateLabel = update.error
    ? 'error'
    : update.update_available
      ? `update v${update.latest_version || '?'}`
      : update.state || 'idle';

  // Collect any active failures so they are visible somewhere in diagnostics.
  const issues = [];
  if (backend.error && backend.status !== 'healthy') {
    issues.push(`Backend (${backend.status}): ${backend.error}${backend.consecutiveFailures ? ` — ${backend.consecutiveFailures} consecutive failure(s)` : ''}`);
  }
  if (database.ok === false && database.error) {
    issues.push(`Database: ${database.error}`);
  }
  if (delivery.status === 'red' || delivery.status === 'yellow') {
    issues.push(`Delivery: ${delivery.detail || delivery.status}`);
  }
  if (update.error) {
    issues.push(`Update: ${update.error}`);
  }
  if (runtime.diagnosticsError) {
    issues.push(`Diagnostics: ${runtime.diagnosticsError}`);
  }

  return (
    <section className="runtime-bar" aria-label="Runtime status">
      <div className="runtime-chips">
        <span className="runtime-chip">
          <span className="runtime-chip-label">Backend</span>
          <Badge tone={connectionTone(backend.status)}>{connectionLabel(backend.status)}</Badge>
        </span>
        <span className="runtime-chip">
          <span className="runtime-chip-label">Database</span>
          <Badge tone={databaseTone}>{databaseLabel}</Badge>
        </span>
        <span className="runtime-chip">
          <span className="runtime-chip-label">Delivery</span>
          <Badge tone={snapshotTone(delivery.status)}>{delivery.status}</Badge>
        </span>
        <span className="runtime-chip">
          <span className="runtime-chip-label">Update</span>
          <Badge tone={updateTone}>{updateLabel}</Badge>
        </span>
      </div>
      {issues.length > 0 && (
        <details className="runtime-issues">
          <summary>{issues.length} active issue{issues.length > 1 ? 's' : ''}</summary>
          <ul>
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
