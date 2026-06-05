import { BASE_URL } from '../api/client.js';

/**
 * The non-binary backend connection state machine.
 *
 *   starting   -> initial mount, before the first probe
 *   connecting -> startup handshake in progress (/health polling)
 *   healthy    -> backend reachable and reporting OK
 *   degraded   -> reachable previously but currently failing transiently
 *   offline    -> unreachable (startup never succeeded, or sustained failures)
 */
export const CONNECTION_STATES = ['starting', 'connecting', 'healthy', 'degraded', 'offline'];

/** Map a backend connection state to a Badge tone. */
export function connectionTone(state) {
  switch (state) {
    case 'healthy':
      return 'success';
    case 'degraded':
    case 'connecting':
    case 'starting':
      return 'warning';
    case 'offline':
      return 'danger';
    default:
      return 'neutral';
  }
}

/** Human-readable label for a backend connection state. */
export function connectionLabel(state) {
  switch (state) {
    case 'starting':
    case 'connecting':
      return 'Connecting…';
    case 'healthy':
      return 'Healthy';
    case 'degraded':
      return 'Degraded';
    case 'offline':
      return 'Offline';
    default:
      return String(state || 'unknown');
  }
}

/**
 * Diagnostic hints surfaced when the startup handshake is taking too long or
 * the backend is unreachable. The most likely cause for the observed error
 * classification is ordered first, but every hint is shown so no failure mode
 * is hidden from the operator.
 *
 * @param {{ kind?: string, message?: string }|null} classification
 * @returns {{ key: string, title: string, detail: string }[]}
 */
export function connectionHints(classification) {
  const hints = [
    {
      key: 'not-started',
      title: 'Backend not started',
      detail: 'The PARIS SENDER backend process may not have launched. Check the backend log for startup errors.'
    },
    {
      key: 'port-conflict',
      title: 'Port conflict',
      detail: `Another process may already be using ${BASE_URL}. Free the port or set PARIS_PORT to a different value.`
    },
    {
      key: 'missing-exe',
      title: 'Missing executable',
      detail: 'The bundled backend executable could not be found or failed to spawn from the app resources.'
    },
    {
      key: 'firewall',
      title: 'Firewall / network issue',
      detail: 'A firewall, VPN, or security tool may be blocking the local connection to the backend.'
    }
  ];

  const kind = classification?.kind;
  if (kind === 'timeout') {
    // A timeout most often points at a firewall/network block.
    return [...hints].sort((a, b) => (a.key === 'firewall' ? -1 : b.key === 'firewall' ? 1 : 0));
  }
  if (kind === 'crash' || kind === 'http') {
    // The process answered but errored — it started but is unhealthy.
    return [...hints].sort((a, b) => (a.key === 'missing-exe' ? -1 : b.key === 'missing-exe' ? 1 : 0));
  }
  return hints;
}

/** Tone for a backend health-snapshot component status (green/yellow/red). */
export function snapshotTone(status) {
  switch (status) {
    case 'green':
      return 'success';
    case 'yellow':
      return 'warning';
    case 'red':
      return 'danger';
    default:
      return 'neutral';
  }
}

/**
 * Derive the delivery-path status from a /diagnostics snapshot. Returns an
 * explicit "unknown" rather than fabricating a healthy state when the snapshot
 * is missing the delivery component.
 *
 * @param {object|null} diagnostics
 * @returns {{ status: string, detail: string|null }}
 */
export function deriveDeliveryStatus(diagnostics) {
  const components = diagnostics?.health?.components;
  if (!Array.isArray(components)) {
    return { status: 'unknown', detail: null };
  }
  const delivery =
    components.find((c) => c.kind === 'delivery') ||
    components.find((c) => /delivery/i.test(c?.name || ''));
  if (!delivery) {
    return { status: 'unknown', detail: null };
  }
  return { status: delivery.status || 'unknown', detail: delivery.detail || null };
}
