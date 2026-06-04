const icons = {
  Dashboard: '⌂',
  Campaigns: '✉',
  Compose: '✍',
  Contacts: '☷',
  Analytics: '▥',
  Settings: '⚙',
  Logs: '☰',
  'Backend Logs': '▤',
  Domains: '◎',
  Deliverability: '◉',
  Warmup: '↗',
  Health: '♥'
};

export default function Sidebar({ screens, active, onChange }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">PS</div>
        <div>
          <strong>Paris Sender</strong>
          <span>Phase 2 Console</span>
        </div>
      </div>
      <nav>
        {screens.map((screen) => (
          <button
            key={screen}
            className={active === screen ? 'nav-item active' : 'nav-item'}
            onClick={() => onChange(screen)}
            type="button"
          >
            <span>{icons[screen] || '•'}</span>
            {screen}
          </button>
        ))}
      </nav>
    </aside>
  );
}
