import { useEffect, useState } from 'react';
import { getCampaign } from '../api/client.js';
import HealthBars from '../components/HealthBars.jsx';

const CAMPAIGN_KEY = 'paris_sender_campaigns';

function readCampaigns() {
  try { return JSON.parse(localStorage.getItem(CAMPAIGN_KEY) || '[]'); } catch { return []; }
}

export default function Analytics() {
  const [campaigns] = useState(readCampaigns);
  const [selectedId, setSelectedId] = useState(campaigns[0]?.id || '');
  const [campaign, setCampaign] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!selectedId) return;
    getCampaign(selectedId).then(setCampaign).catch((err) => setError(err.message));
  }, [selectedId]);

  const rollups = campaign?.status_rollups || {};
  const max = Math.max(1, ...Object.values(rollups));

  return (
    <div className="grid two">
      <section className="card">
        <h2>Campaign</h2>
        <div className="form-row">
          <label>Select campaign</label>
          <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
            <option value="">Choose campaign</option>
            {campaigns.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </div>
        {campaign && <pre className="code">{JSON.stringify({ id: campaign.id, name: campaign.name }, null, 2)}</pre>}
        {campaigns.length === 0 && <p className="muted">Create a campaign first to visualize delivery analytics.</p>}
        {error && <div className="notice danger">{error}</div>}
      </section>
      <section className="card">
        <h2>Status rollups</h2>
        <HealthBars data={rollups} max={max} />
      </section>
    </div>
  );
}
