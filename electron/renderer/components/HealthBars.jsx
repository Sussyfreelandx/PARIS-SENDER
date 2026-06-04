export default function HealthBars({ data, max = 100, compact = false }) {
  const entries = Array.isArray(data) ? data : Object.entries(data || {}).map(([label, value]) => ({ label, value }));
  return (
    <div className={compact ? 'bars compact' : 'bars'}>
      {entries.map((item) => {
        const label = item.label || item.recorded_at || 'item';
        const value = Number(item.value ?? item.health_score ?? 0);
        const width = Math.max(2, Math.min(100, max ? (value / max) * 100 : value));
        return (
          <div className="bar-row" key={`${label}-${value}`} title={`${label}: ${value}`}>
            <span>{label}</span>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${width}%` }} />
            </div>
            <strong>{value}</strong>
          </div>
        );
      })}
    </div>
  );
}
