import { useEffect, useState } from 'react';
import { getCampaign, getDomains, getHealth } from '../api/client.js';
import Badge from '../components/Badge.jsx';
import HealthBars from '../components/HealthBars.jsx';

const CAMPAIGN_KEY = 'paris_sender_campaigns';

function readCampaignRefs() {
  try { return JSON.parse(localStorage.getItem(CAMPAIGN_KEY) || '[]'); } catch { return []; }
}

export default function Dashboard({ onNavigate }) {
  const [health, setHealth] = useState(null);
  const [domains, setDomains] = useState([]);
  const [campaigns, setCampaigns] = useState([]);

  useEffect(() => {
    async function load() {
      const [healthResult, domainsResult] = await Promise.allSettled([getHealth(), getDomains()]);
      if (healthResult.status === 'fulfilled') setHealth(healthResult.value);
      if (domainsResult.status === 'fulfilled') setDomains(domainsResult.value.domains || []);

      const refs = readCampaignRefs().slice(0, 5);
      const loaded = await Promise.allSettled(refs.map((campaign) => getCampaign(campaign.id)));
      setCampaigns(loaded.filter((item) => item.status === 'fulfilled').map((item) => item.value));
    }
    load();
  }, []);

  const verifiedDomains = domains.filter((domain) => domain.is_verified);
  const totals = campaigns.reduce((acc, campaign) => {
    Object.entries(campaign.status_rollups || {}).forEach(([key, value]) => {
      acc[key] = (acc[key] || 0) + Number(value || 0);
    });
    return acc;
  }, {});

  return (
    <div className="grid">
      <div className="grid three">
        <section className="card">
          <p className="eyebrow">Backend</p>
          <p className="metric">{health?.status || 'unknown'}</p>
        </section>
        <section className="card">
          <p className="eyebrow">Verified domains</p>
          <p className="metric">{verifiedDomains.length}</p>
        </section>
        <section className="card">
          <p className="eyebrow">Tracked campaigns</p>
          <p className="metric">{campaigns.length}</p>
        </section>
      </div>

      <section className="card">
        <div className="card-header">
          <h2>Quick links</h2>
        </div>
        <div className="actions">
          {['Compose', 'Campaigns', 'Contacts', 'Domains'].map((screen) => (
            <button className="secondary" key={screen} onClick={() => onNavigate(screen)} type="button">Open {screen}</button>
          ))}
        </div>
      </section>

      <div className="grid two">
        <section className="card">
          <h2>Recent campaigns</h2>
          <div className="list">
            {campaigns.length === 0 && <p className="muted">Create a campaign to see rollups here.</p>}
            {campaigns.map((campaign) => (
              <div className="list-item" key={campaign.id}>
                <strong>{campaign.name}</strong>
                <p className="muted">{campaign.id}</p>
                <Badge>{Object.values(campaign.status_rollups || {}).reduce((sum, value) => sum + Number(value || 0), 0)} events</Badge>
              </div>
            ))}
          </div>
        </section>
        <section className="card">
          <h2>Aggregate status</h2>
          <HealthBars data={totals} max={Math.max(1, ...Object.values(totals))} />
        </section>
      </div>
    </div>
  );
}
