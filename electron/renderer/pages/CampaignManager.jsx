import { useEffect, useMemo, useState } from 'react';
import { createCampaign, deleteCampaign, getCampaign, getDomains, sendCampaign } from '../api/client.js';
import { StatusBadge } from '../components/Badge.jsx';
import HealthBars from '../components/HealthBars.jsx';
import { readAttachments, subscribeAttachments, toApiAttachments } from '../api/attachments.js';

const CAMPAIGN_KEY = 'paris_sender_campaigns';
const CONTACT_KEY = 'paris_sender_contacts';
const SETTINGS_KEY = 'paris_sender_settings';

const EMAIL_RE = /.+@.+\..+/;

function readJson(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback)); } catch { return fallback; }
}

function writeCampaignRef(campaign) {
  const current = readJson(CAMPAIGN_KEY, []);
  const next = [campaign, ...current.filter((item) => item.id !== campaign.id)].slice(0, 25);
  localStorage.setItem(CAMPAIGN_KEY, JSON.stringify(next));
  return next;
}

function removeCampaignRef(id) {
  const next = readJson(CAMPAIGN_KEY, []).filter((item) => item.id !== id);
  localStorage.setItem(CAMPAIGN_KEY, JSON.stringify(next));
  return next;
}

function parseRecipients(text) {
  const seen = new Set();
  text
    .split(/[\s,;]+/)
    .map((value) => value.trim().toLowerCase())
    .filter((value) => EMAIL_RE.test(value))
    .forEach((value) => seen.add(value));
  return [...seen];
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
  const [recipientsText, setRecipientsText] = useState(() => readJson(CONTACT_KEY, []).join('\n'));
  const [html, setHtml] = useState(false);
  const [nonSmtpDelivery, setNonSmtpDelivery] = useState(() => Boolean(readJson(SETTINGS_KEY, {}).nonSmtpDefault));
  const [attachments, setAttachments] = useState(readAttachments);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => subscribeAttachments(setAttachments), []);

  const recipients = useMemo(() => parseRecipients(recipientsText), [recipientsText]);
  const invalidCount = useMemo(
    () => recipientsText.split(/[\s,;]+/).map((value) => value.trim()).filter((value) => value && !EMAIL_RE.test(value)).length,
    [recipientsText]
  );
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
    if (!selectedId) { setSelectedCampaign(null); return; }
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

  async function onDelete(id) {
    setError('');
    if (!window.confirm('Delete this campaign and its delivery history? This cannot be undone.')) return;
    try {
      await deleteCampaign(id);
    } catch (err) {
      // A campaign that only exists locally (never persisted) yields 404; still drop the local ref.
      if (!/not found/i.test(err.message)) { setError(err.message); return; }
    }
    const refs = removeCampaignRef(id);
    setCampaignRefs(refs);
    if (selectedId === id) {
      const nextId = refs[0]?.id || '';
      setSelectedId(nextId);
      setResult(null);
    }
  }

  function loadContacts() {
    const contacts = readJson(CONTACT_KEY, []);
    const merged = parseRecipients([recipientsText, ...contacts].join('\n'));
    setRecipientsText(merged.join('\n'));
  }

  const disabledReasons = useMemo(() => {
    const reasons = [];
    if (!selectedId) reasons.push('Create or select a campaign.');
    if (verifiedDomains.length === 0) reasons.push('Verify a sender domain in Domain Manager.');
    if (!sender) reasons.push('Choose a verified sender domain.');
    if (recipients.length === 0) reasons.push('Add at least one valid recipient.');
    if (!subject.trim()) reasons.push('Enter a subject line.');
    if (!content.trim()) reasons.push('Enter message content.');
    return reasons;
  }, [selectedId, verifiedDomains.length, sender, recipients.length, subject, content]);

  const canSend = disabledReasons.length === 0 && !sending;

  async function onSend() {
    setError('');
    setResult(null);
    setSending(true);
    try {
      const settings = readJson(SETTINGS_KEY, {});
      const payload = {
        recipients,
        subject,
        content,
        sender,
        html,
        non_smtp_delivery: nonSmtpDelivery,
        attachments: toApiAttachments(attachments)
      };
      if (nonSmtpDelivery) {
        const nonSmtp = settings.nonSmtp || {};
        payload.non_smtp = {
          port: Number(nonSmtp.port) || 25,
          ...(nonSmtp.helo ? { helo_hostname: nonSmtp.helo } : {})
        };
      } else if (settings.smtp && settings.smtp.host) {
        const smtp = settings.smtp;
        payload.smtp = {
          host: smtp.host,
          port: Number(smtp.port) || 587,
          use_tls: Boolean(smtp.use_tls),
          use_ssl: Boolean(smtp.use_ssl),
          ...(smtp.username ? { username: smtp.username } : {}),
          ...(smtp.password ? { password: smtp.password } : {})
        };
      }
      const response = await sendCampaign(selectedId, payload);
      setResult(response);
      const fresh = await getCampaign(selectedId);
      setSelectedCampaign(fresh);
    } catch (err) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="stack">
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
              <div className="list-item card-header" key={campaign.id}>
                <button className={selectedId === campaign.id ? 'nav-item active' : 'nav-item'} onClick={() => setSelectedId(campaign.id)} type="button">
                  {campaign.name}
                </button>
                <button className="ghost small" onClick={() => onDelete(campaign.id)} type="button">Delete</button>
              </div>
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
      </div>

      <section className="card">
        <h2>Compose &amp; send</h2>
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
        <div className="form-row">
          <label>Subject</label>
          <input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="Subject line" />
        </div>
        <div className="form-row">
          <label>Content</label>
          <textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="Write your message. Use [firstname] and other placeholders for personalization." />
        </div>
        <div className="form-row">
          <div className="card-header">
            <label>Bulk recipient list ({recipients.length} valid{invalidCount > 0 ? `, ${invalidCount} invalid` : ''})</label>
            <div className="actions">
              <button className="ghost small" onClick={loadContacts} type="button">Load from Contacts</button>
              <button className="ghost small" onClick={() => setRecipientsText('')} type="button">Clear</button>
            </div>
          </div>
          <textarea value={recipientsText} onChange={(event) => setRecipientsText(event.target.value)} placeholder="alice@example.com&#10;bob@example.com" />
        </div>
        <div className="actions">
          <label className="switch"><input type="checkbox" checked={html} onChange={(event) => setHtml(event.target.checked)} /> HTML content</label>
          <label className="switch"><input type="checkbox" checked={nonSmtpDelivery} onChange={(event) => setNonSmtpDelivery(event.target.checked)} /> Non-SMTP delivery</label>
        </div>
        <p className="muted">Sender: {sender || 'choose a domain'}. {nonSmtpDelivery ? 'Channel: non-SMTP (direct MX).' : 'Channel: SMTP.'}</p>
        <p className="muted">Attachments: {attachments.length}{attachments.length > 0 ? ` (${attachments.map((item) => item.filename).join(', ')})` : ''}.</p>
        <button className="primary" onClick={onSend} disabled={!canSend} type="button">{sending ? 'Sending…' : `Send campaign (${recipients.length})`}</button>
        {!canSend && !sending && disabledReasons.length > 0 && (
          <ul className="muted send-reasons">
            {disabledReasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        )}
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
