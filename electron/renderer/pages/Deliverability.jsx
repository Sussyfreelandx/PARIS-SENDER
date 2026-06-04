import { useEffect, useMemo, useState } from 'react';
import { getCampaignScore, predictCampaign } from '../api/client.js';
import Badge from '../components/Badge.jsx';
import HealthBars from '../components/HealthBars.jsx';

const CAMPAIGN_KEY = 'paris_sender_campaigns';

function readJson(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback)); } catch { return fallback; }
}

function scoreTone(score, threshold) {
  if (score >= threshold) return 'success';
  if (score >= Math.max(0, threshold - 15)) return 'warning';
  return 'danger';
}

function ScoreCard({ title, score }) {
  if (!score) return <p className="muted">No score loaded yet.</p>;
  const bars = (score.components || []).map((component) => ({ label: component.name, value: component.score }));
  return (
    <div className="list">
      <div className="card-header">
        <div>
          <p className="eyebrow">{title}</p>
          <p className="metric">{score.score}</p>
        </div>
        <Badge tone={scoreTone(score.score, score.threshold)}>{score.passed ? 'Pass' : 'Block'} ≥ {score.threshold}</Badge>
      </div>
      <HealthBars data={bars} />
      <table className="table">
        <thead><tr><th>Component</th><th>Score</th><th>Weight</th><th>Detail</th></tr></thead>
        <tbody>
          {(score.components || []).map((component) => (
            <tr key={component.name}>
              <td><strong>{component.name}</strong></td>
              <td>{component.score}</td>
              <td>{component.weight}%</td>
              <td className="muted">{component.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {score.warnings?.length > 0 && <div className="notice warning"><strong>Warnings</strong><ul>{score.warnings.map((item) => <li key={item}>{item}</li>)}</ul></div>}
      {score.suggestions?.length > 0 && <div className="notice"><strong>Suggestions</strong><ul>{score.suggestions.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    </div>
  );
}

export default function Deliverability() {
  const [campaignRefs, setCampaignRefs] = useState(() => readJson(CAMPAIGN_KEY, []));
  const [selectedId, setSelectedId] = useState(campaignRefs[0]?.id || '');
  const [manualId, setManualId] = useState('');
  const [score, setScore] = useState(null);
  const [prediction, setPrediction] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    subject: 'A note from Paris Sender',
    content: 'Hello [firstname],\n\nThis is a clean campaign preview.',
    sender: 'sender@example.com',
    recipients: 'a@example.com',
    html: false
  });

  const selectedCampaign = useMemo(() => campaignRefs.find((item) => String(item.id) === String(selectedId)), [campaignRefs, selectedId]);

  useEffect(() => {
    const refs = readJson(CAMPAIGN_KEY, []);
    setCampaignRefs(refs);
    if (!selectedId && refs[0]?.id) setSelectedId(refs[0].id);
  }, []);

  async function withBusy(action) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function loadScore(id = selectedId) {
    if (!id) return;
    await withBusy(async () => setScore(await getCampaignScore(id)));
  }

  async function runPrediction(event) {
    event.preventDefault();
    if (!selectedId) return;
    await withBusy(async () => {
      const payload = {
        subject: form.subject,
        content: form.content,
        sender: form.sender,
        html: form.html,
        recipients: form.recipients.split(/[\s,]+/).filter(Boolean)
      };
      setPrediction(await predictCampaign(selectedId, payload));
    });
  }

  function selectManual(event) {
    event.preventDefault();
    if (!manualId.trim()) return;
    setSelectedId(manualId.trim());
    setScore(null);
    setPrediction(null);
  }

  return (
    <div className="grid two">
      {error && <div className="notice danger">{error}</div>}
      <section className="card">
        <div className="card-header">
          <h2>Campaign score</h2>
          <button className="ghost small" onClick={() => loadScore()} disabled={!selectedId || busy} type="button">Refresh</button>
        </div>
        <p className="muted">Choose a locally tracked campaign or enter an id. Scores include domain, content, history, spam proxy, and engagement signals.</p>
        <div className="list">
          {campaignRefs.map((campaign) => (
            <button className={String(selectedId) === String(campaign.id) ? 'nav-item active' : 'nav-item'} key={campaign.id} onClick={() => { setSelectedId(campaign.id); setScore(null); }} type="button">
              {campaign.name} <span className="muted">#{campaign.id}</span>
            </button>
          ))}
          {campaignRefs.length === 0 && <p className="muted">No campaigns tracked in this browser yet.</p>}
        </div>
        <form onSubmit={selectManual} style={{ marginTop: 16 }}>
          <div className="form-row inline">
            <div><label>Campaign id</label><input value={manualId} onChange={(event) => setManualId(event.target.value)} placeholder="1" /></div>
            <div className="actions" style={{ alignItems: 'end' }}><button className="secondary" type="submit">Use id</button></div>
          </div>
        </form>
        {selectedId && <p className="muted">Selected: {selectedCampaign?.name || 'campaign'} #{selectedId}</p>}
        <ScoreCard title="Persisted campaign" score={score} />
      </section>

      <section className="card">
        <h2>Predict Before Send</h2>
        <form onSubmit={runPrediction}>
          <div className="form-row"><label>Sender</label><input value={form.sender} onChange={(event) => setForm({ ...form, sender: event.target.value })} required /></div>
          <div className="form-row"><label>Recipients</label><textarea value={form.recipients} onChange={(event) => setForm({ ...form, recipients: event.target.value })} placeholder="one@example.com, two@example.com" /></div>
          <div className="form-row"><label>Subject</label><input value={form.subject} onChange={(event) => setForm({ ...form, subject: event.target.value })} /></div>
          <div className="form-row"><label>Content</label><textarea value={form.content} onChange={(event) => setForm({ ...form, content: event.target.value })} required /></div>
          <div className="actions">
            <label className="switch"><input type="checkbox" checked={form.html} onChange={(event) => setForm({ ...form, html: event.target.checked })} /> HTML content</label>
            <button className="primary" disabled={!selectedId || busy} type="submit">Predict score</button>
          </div>
        </form>
        <ScoreCard title="Simulation" score={prediction} />
      </section>
    </div>
  );
}
