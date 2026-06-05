import { useMemo, useState } from 'react';
import Sidebar from './components/Sidebar.jsx';
import Badge from './components/Badge.jsx';
import LoadingScreen from './components/LoadingScreen.jsx';
import UpdateBanner from './components/UpdateBanner.jsx';
import RuntimeStatusBar from './components/RuntimeStatusBar.jsx';
import Dashboard from './pages/Dashboard.jsx';
import CampaignManager from './pages/CampaignManager.jsx';
import ComposeEditor from './pages/ComposeEditor.jsx';
import Contacts from './pages/Contacts.jsx';
import Analytics from './pages/Analytics.jsx';
import Settings from './pages/Settings.jsx';
import Logs from './pages/Logs.jsx';
import DomainManager from './pages/DomainManager.jsx';
import Deliverability from './pages/Deliverability.jsx';
import Warmup from './pages/Warmup.jsx';
import HealthMonitor from './pages/HealthMonitor.jsx';
import ServerLogs from './pages/ServerLogs.jsx';
import Diagnostics from './pages/Diagnostics.jsx';
import useBackendLifecycle from './hooks/useBackendLifecycle.js';
import useUpdateStatus from './hooks/useUpdateStatus.js';
import { connectionHints, connectionLabel, connectionTone, deriveDeliveryStatus } from './lib/runtimeStatus.js';

const screens = ['Dashboard', 'Campaigns', 'Compose', 'Contacts', 'Analytics', 'Settings', 'Logs', 'Backend Logs', 'Domains', 'Deliverability', 'Warmup', 'Health', 'Diagnostics'];

export default function App() {
  const [active, setActive] = useState('Dashboard');

  // Resilient, non-binary backend lifecycle:
  // starting -> connecting -> healthy -> degraded -> offline (with recovery).
  const { phase, backend, diagnostics, showHints, everConnected, retry } = useBackendLifecycle();

  // Centralized, reactive auto-update status that ties the version display to
  // the update system (current/latest version, availability, channel).
  const update = useUpdateStatus();

  // Central runtime status object: the single source of truth consumed by the
  // UI. Every value is a real probe result — no fabricated states.
  const runtime = useMemo(() => ({
    backend: { ...backend, phase },
    update: {
      current_version: update.current_version,
      latest_version: update.latest_version,
      update_available: update.update_available,
      update_channel: update.update_channel,
      state: update.state,
      error: update.error,
      status: update.status
    },
    database: diagnostics
      ? { ok: diagnostics.database ? Boolean(diagnostics.database.ok) : null, error: diagnostics.database?.error || null }
      : { ok: null, error: null },
    delivery: deriveDeliveryStatus(diagnostics),
    diagnosticsError: diagnostics?._error || null
  }), [backend, phase, update, diagnostics]);

  const page = useMemo(() => {
    switch (active) {
      case 'Campaigns': return <CampaignManager />;
      case 'Compose': return <ComposeEditor />;
      case 'Contacts': return <Contacts />;
      case 'Analytics': return <Analytics />;
      case 'Settings': return <Settings />;
      case 'Logs': return <Logs />;
      case 'Backend Logs': return <ServerLogs />;
      case 'Domains': return <DomainManager />;
      case 'Deliverability': return <Deliverability />;
      case 'Warmup': return <Warmup />;
      case 'Health': return <HealthMonitor />;
      case 'Diagnostics': return <Diagnostics />;
      default: return <Dashboard onNavigate={setActive} />;
    }
  }, [active]);

  // Keep the main UI hidden during the initial handshake (and while offline if
  // we never connected). Once the backend has connected at least once we keep
  // the app mounted and surface degraded/offline via the runtime status bar so
  // a transient blip never tears the whole UI down to the boot screen.
  const blockingBoot = phase === 'starting' || phase === 'connecting' || (phase === 'offline' && !everConnected);
  if (blockingBoot) {
    return (
      <LoadingScreen
        state={phase === 'offline' ? 'offline' : 'connecting'}
        error={backend.error}
        classification={backend.classification}
        hints={connectionHints(backend.classification)}
        showHints={showHints}
        onRetry={retry}
      />
    );
  }

  const versionLabel = `v${update.current_version || '0.2.0'}`;

  return (
    <div className="app-shell">
      <Sidebar screens={screens} active={active} onChange={setActive} />
      <main className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">PARIS SENDER</p>
            <h1>{active}</h1>
          </div>
          <div className="topbar-actions">
            <Badge tone={connectionTone(backend.status)}>Backend: {connectionLabel(backend.status)}</Badge>
            <span className="muted">{versionLabel}</span>
            {update.update_available && (
              <Badge tone="warning">Update v{update.latest_version || '?'} available</Badge>
            )}
          </div>
        </header>
        <RuntimeStatusBar runtime={runtime} />
        {backend.status === 'degraded' && (
          <div className="notice warning">
            Backend degraded: {backend.error || 'recent health checks failed'} ({backend.consecutiveFailures} consecutive failure(s)). Retrying…
          </div>
        )}
        {backend.status === 'offline' && (
          <div className="notice danger">
            Backend offline: {backend.error || 'health checks are failing'}. The app will recover automatically when the backend responds.
          </div>
        )}
        <UpdateBanner status={update.status} onInstall={update.install} />
        {page}
      </main>
    </div>
  );
}
