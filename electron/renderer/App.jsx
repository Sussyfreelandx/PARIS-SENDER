import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getHealth, getHealthWithRetry } from './api/client.js';
import Sidebar from './components/Sidebar.jsx';
import Badge from './components/Badge.jsx';
import LoadingScreen from './components/LoadingScreen.jsx';
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

const screens = ['Dashboard', 'Campaigns', 'Compose', 'Contacts', 'Analytics', 'Settings', 'Logs', 'Backend Logs', 'Domains', 'Deliverability', 'Warmup', 'Health'];

export default function App() {
  const [active, setActive] = useState('Dashboard');
  // Backend connection state machine: starting -> connecting -> online | offline.
  const [connection, setConnection] = useState('starting');
  const [health, setHealth] = useState({ status: 'starting' });
  const [connError, setConnError] = useState(null);
  const cancelledRef = useRef(false);

  // Drive the startup handshake: retry /health silently (up to 30s) and only
  // transition to "offline" once every attempt has failed. The main UI stays
  // hidden behind the connecting screen until we reach "online", so a transient
  // "Failed to fetch" during boot never flashes an error to the user.
  const connect = useCallback(async () => {
    cancelledRef.current = false;
    setConnError(null);
    setConnection('connecting');
    setHealth({ status: 'connecting' });
    try {
      const result = await getHealthWithRetry({
        attempts: 30,
        intervalMs: 1000,
        onRetry: () => {
          if (!cancelledRef.current) setConnection('connecting');
        }
      });
      if (cancelledRef.current) return;
      setHealth(result);
      setConnection('online');
    } catch (error) {
      if (cancelledRef.current) return;
      setConnError(error.message);
      setHealth({ status: 'offline', error: error.message });
      setConnection('offline');
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      cancelledRef.current = true;
    };
  }, [connect]);

  // Steady-state polling, only after the initial handshake succeeds. A single
  // transient failure here updates the status badge but does NOT tear the app
  // back down to the connecting screen.
  useEffect(() => {
    if (connection !== 'online') return undefined;
    let cancelled = false;
    async function pollHealth() {
      try {
        const result = await getHealth();
        if (!cancelled) setHealth(result);
      } catch (error) {
        if (!cancelled) setHealth({ status: 'offline', error: error.message });
      }
    }
    const timer = window.setInterval(pollHealth, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [connection]);

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
      default: return <Dashboard onNavigate={setActive} />;
    }
  }, [active]);

  const healthTone = health.status === 'ok' || health.status === 'healthy'
    ? 'success'
    : health.status === 'checking' || health.status === 'starting' || health.status === 'connecting'
      ? 'warning'
      : 'danger';
  const healthLabel = health.status === 'connecting' || health.status === 'starting'
    ? 'Starting backend...'
    : `Backend: ${health.status}`;

  // Keep the main UI hidden until the backend handshake resolves. This is the
  // dedicated "Connecting..." screen that prevents any error flicker on launch.
  if (connection !== 'online') {
    return <LoadingScreen state={connection} error={connError} onRetry={connect} />;
  }

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
            <Badge tone={healthTone}>{healthLabel}</Badge>
            <span className="muted">v{window.parisAPI?.appVersion || '0.2.0'}</span>
          </div>
        </header>
        {health.error && <div className="notice danger">Backend health check failed: {health.error}</div>}
        {page}
      </main>
    </div>
  );
}
