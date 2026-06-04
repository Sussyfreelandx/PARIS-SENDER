import { useEffect, useMemo, useState } from 'react';
import { getHealth } from './api/client.js';
import Sidebar from './components/Sidebar.jsx';
import Badge from './components/Badge.jsx';
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
  const [health, setHealth] = useState({ status: 'checking' });

  useEffect(() => {
    let cancelled = false;
    async function checkHealth() {
      try {
        const result = await getHealth();
        if (!cancelled) setHealth(result);
      } catch (error) {
        if (!cancelled) setHealth({ status: 'offline', error: error.message });
      }
    }
    checkHealth();
    const timer = window.setInterval(checkHealth, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

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

  const healthTone = health.status === 'ok' || health.status === 'healthy' ? 'success' : health.status === 'checking' ? 'warning' : 'danger';

  return (
    <div className="app-shell">
      <Sidebar screens={screens} active={active} onChange={setActive} />
      <main className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">Electron + React</p>
            <h1>{active}</h1>
          </div>
          <div className="topbar-actions">
            <Badge tone={healthTone}>Backend: {health.status}</Badge>
            <span className="muted">v{window.parisAPI?.appVersion || '0.2.0'}</span>
          </div>
        </header>
        {health.error && <div className="notice danger">Backend health check failed: {health.error}</div>}
        {page}
      </main>
    </div>
  );
}
