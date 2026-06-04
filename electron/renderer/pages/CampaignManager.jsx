import { useEffect, useMemo, useState } from 'react';
import { createCampaign, getCampaign, getDomains, sendCampaign } from '../api/client.js';
import { StatusBadge } from '../components/Badge.jsx';
import HealthBars from '../components/HealthBars.jsx';

const CAMPAIGN_KEY = 'paris_sender_campaigns';
const CONTACT_KEY = 'paris_sender_contacts';
const SETTINGS_KEY = 'paris_sender_settings';

function readJson(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback)); } catch { return fallback; }
}

function writeCampaignRef(campaign) {
  const current = readJson(CAMPAIGN_KEY, []);
  const next = [campaign, ...current.filter((item) => item.id !== campaign.id)].slice(0, 25);
  localStorage.setItem(CAMPAIGN_KEY, JSON.stringify(next));
  return next;
}

export default function CampaignManager() {
  const [name, setName] = useState('');
  const [campaignRefs, setCampaignRefs] = useState(() => readJson(CAMPAIGN_KEY, []));
  const [selectedId, setSelectedId] = useState(campaignRefs[0]?.id || '');
  const [selectedCampaign, setSelectedCampaign] = useState(null);
  const [domains, setDomains] = useState([]);
  const [domainName, setDomainName] = useState('');
  const [localPart, setLocalPart] = useState('noreply');
  const [subject, setSubject] = useState('A note from PARIS SENDER');
  const [content, setContent] = useState('Hello [firstname],\n\nHere is the latest campaign update.');
  const [html, setHtml] = useState(false);
  const [nonSmtpDelivery, setNonSmtpDelivery] = useState(() => Boolean(readJson(SETTINGS_KEY, {}).nonSmtpDefault));
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const contacts = readJson(CONTACT_KEY, []);
  const verifiedDomains = useMemo(() => domains.filter((domain) => domain.is_verified), [domains]);
  const sender = domainName ? `${localPart || 'noreply'}@${domainName}` : '';

  useEffect(() => {
    async function loadDomains() {
      const data = await getDomains();
      setDomains(data.domains || []);
      const firstVerified = (data.domains || []).find((domain) => domain.is_verified);
      setDomainName((current) => current || firstVerified?.name || '');
    }
    loadDomains().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    getCampaign(selectedId).then(setSelectedCampaign).catch((err) => setError(err.message));
  }, [selectedId]);

  async function onCreate(event) {
    event.preventDefault();
    setError('');
    try {
      const campaign = await createCampaign(name.trim());
      const refs = writeCampaignRef(campaign);
      setCampaignRefs(refs);
      setSelectedId(campaign.id);
      setName('');
    } catch (err) {
      setError(err.message);
    }
  }

  async function onSend() {
    setError('');
    setResult(null);
    try {
      const payload = { recipients: contacts, subject, content, sender, html, non_smtp_delivery: nonSmtpDelivery };
      const response = await sendCampaign(selectedId, payload);
      setResult(response);
      const fresh = await getCampaign(selectedId);
      setSelectedCampaign(fresh);
    } catch (err) {
      setError(err.message);
    }
  }

  const canSend = Boolean(selectedId && sender && verifiedDomains.length > 0 && contacts.length > 0);

  return (
    <div className="grid two">
      <section className="card">
        <h2>Create campaign</h2>
        <form onSubmit={onCreate}>
          <div className="form-row">
            <label>Campaign name</label>
            <input value={name} onChange={(event) => setName(event.target.value)} required />
          </div>
          <button className="primary" type="submit">Create</button>
        </form>
        <h3>Tracked campaigns</h3>
        <div className="list">
          {campaignRefs.map((campaign) => (
            <button className={selectedId === campaign.id ? 'nav-item active' : 'nav-item'} key={campaign.id} onClick={() => setSelectedId(campaign.id)} type="button">
              {campaign.name}
            </button>
          ))}
          {campaignRefs.length === 0 && <p className="muted">No campaigns tracked in this browser yet.</p>}
        </div>
      </section>

      <section className="card">
        <h2>Selected campaign</h2>
        {selectedCampaign ? (
          <>
            <div className="card-header"><strong>{selectedCampaign.name}</strong><StatusBadge status="selected" /></div>
            <HealthBars data={selectedCampaign.status_rollups || {}} max={Math.max(1, ...Object.values(selectedCampaign.status_rollups || {}))} />
          </>
        ) : <p className="muted">Select or create a campaign.</p>}
      </section>

      <section className="card">
        <h2>Send campaign</h2>
        {verifiedDomains.length === 0 && <div className="notice warning">No verified sender domain is available. Verify a domain in Domain Manager before sending.</div>}
        <div className="form-row inline">
          <div>
            <label>Sender local part</label>
            <input value={localPart} onChange={(event) => setLocalPart(event.target.value.replace(/@.*/, ''))} />
          </div>
          <div>
            <label>Verified domain</label>
            <select value={domainName} onChange={(event) => setDomainName(event.target.value)} disabled={verifiedDomains.length === 0}>
              <option value="">Choose verified domain</option>
              {verifiedDomains.map((domain) => <option key={domain.id} value={domain.name}>{domain.name}</option>)}
            </select>
          </div>
        </div>
        <div className="form-row"><label>Subject</label><input value={subject} onChange={(event) => setSubject(event.target.value)} /></div>
        <div className="form-row"><label>Content</label><textarea value={content} onChange={(event) => setContent(event.target.value)} /></div>
        <div className="actions">
          <label className="switch"><input type="checkbox" checked={html} onChange={(event) => setHtml(event.target.checked)} /> HTML content</label>
          <label className="switch"><input type="checkbox" checked={nonSmtpDelivery} onChange={(event) => setNonSmtpDelivery(event.target.checked)} /> Non-SMTP delivery</label>
        </div>
        <p className="muted">Recipients loaded from Contacts: {contacts.length}. Sender: {sender || 'choose a domain'}.</p>
        <button className="primary" onClick={onSend} disabled={!canSend} type="button">Send campaign</button>
        {result && <pre className="code">{JSON.stringify(result, null, 2)}</pre>}
        {error && <div className="notice danger">{error}</div>}
      </section>

      <section className="card">
        <h2>Verified sender domains</h2>
        <div className="list">
          {verifiedDomains.map((domain) => <div className="list-item" key={domain.id}><strong>{domain.name}</strong><p className="muted">Health score {domain.health_score ?? 'n/a'}</p></div>)}
          {verifiedDomains.length === 0 && <p className="muted">Only domains with is_verified=true appear here.</p>}
        </div>
      </section>
    </div>
  );
}
