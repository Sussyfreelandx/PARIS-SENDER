import { useEffect, useMemo, useState } from 'react';
import { createDomain, deleteDomain, getDomain, getDomainHistory, getDomains, rotateDkim, updateDmarcPolicy, verifyDomain } from '../api/client.js';
import { StatusBadge, VerifiedBadge } from '../components/Badge.jsx';
import CopyButton from '../components/CopyButton.jsx';
import HealthBars from '../components/HealthBars.jsx';

const initialForm = { name: '', selector: 'default', dmarc_policy: 'none', spf_includes: '' };

export default function DomainManager() {
  const [domains, setDomains] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState(null);
  const [history, setHistory] = useState([]);
  const [form, setForm] = useState(initialForm);
  const [policy, setPolicy] = useState('none');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const selectedRecords = useMemo(() => selected?.records || [], [selected]);

  async function loadDomains() {
    const data = await getDomains();
    setDomains(data.domains || []);
    setSelectedId((current) => current || data.domains?.[0]?.id || '');
  }

  async function loadSelected(id) {
    if (!id) {
      setSelected(null);
      setHistory([]);
      return;
    }
    const [domainResult, historyResult] = await Promise.all([getDomain(id), getDomainHistory(id).catch(() => ({ history: [] }))]);
    setSelected(domainResult);
    setPolicy(domainResult.dmarc_policy || 'none');
    setHistory(historyResult.history || []);
  }

  useEffect(() => {
    loadDomains().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    loadSelected(selectedId).catch((err) => setError(err.message));
  }, [selectedId]);

  async function withBusy(action) {
    setBusy(true);
    setError('');
    try {
      const nextSelectedId = await action();
      await loadDomains();
      const idToLoad = nextSelectedId === undefined ? selectedId : nextSelectedId;
      if (idToLoad) await loadSelected(idToLoad);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function addDomain(event) {
    event.preventDefault();
    await withBusy(async () => {
      const payload = { ...form, spf_includes: form.spf_includes.split(/[\s,]+/).filter(Boolean) };
      const created = await createDomain(payload);
      setForm(initialForm);
      setSelectedId(created.id);
      return created.id;
    });
  }

  return (
    <div className="grid">
      {error && <div className="notice danger">{error}</div>}
      <section className="card">
        <div className="card-header">
          <h2>Domains</h2>
          <button className="ghost small" onClick={() => withBusy(loadDomains)} disabled={busy} type="button">Refresh</button>
        </div>
        <table className="table">
          <thead><tr><th>Name</th><th>Status</th><th>Health</th><th>Verification</th><th>Checked</th></tr></thead>
          <tbody>
            {domains.map((domain) => (
              <tr key={domain.id} onClick={() => setSelectedId(domain.id)} style={{ cursor: 'pointer' }}>
                <td><strong>{domain.name}</strong></td>
                <td><StatusBadge status={domain.status} /></td>
                <td>{domain.health_score ?? 'n/a'}</td>
                <td className="actions">
                  <VerifiedBadge verified={domain.dkim_verified} label="DKIM" />
                  <VerifiedBadge verified={domain.spf_verified} label="SPF" />
                  <VerifiedBadge verified={domain.dmarc_verified} label="DMARC" />
                </td>
                <td className="muted">{domain.last_checked_at || 'never'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid two">
        <section className="card">
          <h2>Add domain</h2>
          <form onSubmit={addDomain}>
            <div className="form-row inline">
              <div><label>Domain name</label><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="example.com" required /></div>
              <div><label>DKIM selector</label><input value={form.selector} onChange={(event) => setForm({ ...form, selector: event.target.value })} required /></div>
            </div>
            <div className="form-row inline">
              <div><label>DMARC policy</label><select value={form.dmarc_policy} onChange={(event) => setForm({ ...form, dmarc_policy: event.target.value })}><option value="none">none</option><option value="quarantine">quarantine</option><option value="reject">reject</option></select></div>
              <div><label>SPF includes</label><input value={form.spf_includes} onChange={(event) => setForm({ ...form, spf_includes: event.target.value })} placeholder="include:_spf.example.com" /></div>
            </div>
            <button className="primary" disabled={busy} type="submit">Add domain</button>
          </form>
        </section>

        <section className="card">
          <h2>Selected domain actions</h2>
          {selected ? (
            <>
              <div className="card-header"><strong>{selected.name}</strong><StatusBadge status={selected.status} /></div>
              <div className="actions">
                <button className="primary" onClick={() => withBusy(async () => setSelected(await verifyDomain(selected.id)))} disabled={busy} type="button">Verify</button>
                <button className="secondary" onClick={() => withBusy(async () => setSelected(await rotateDkim(selected.id)))} disabled={busy} type="button">Rotate DKIM</button>
                <button className="danger" onClick={() => withBusy(async () => { await deleteDomain(selected.id); setSelectedId(''); setSelected(null); return ''; })} disabled={busy} type="button">Delete</button>
              </div>
              <div className="form-row" style={{ marginTop: 16 }}>
                <label>DMARC policy</label>
                <div className="actions">
                  <select value={policy} onChange={(event) => setPolicy(event.target.value)}><option value="none">none</option><option value="quarantine">quarantine</option><option value="reject">reject</option></select>
                  <button className="secondary" onClick={() => withBusy(async () => setSelected(await updateDmarcPolicy(selected.id, policy)))} disabled={busy} type="button">Save policy</button>
                </div>
              </div>
            </>
          ) : <p className="muted">Select a domain to manage verification, DKIM, DMARC, and deletion.</p>}
        </section>
      </div>

      {selected && (
        <div className="grid two">
          <section className="card">
            <h2>Required DNS records</h2>
            <table className="table">
              <thead><tr><th>Type</th><th>Host</th><th>Value</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {selectedRecords.map((record) => (
                  <tr key={`${record.record_type}-${record.host}`}>
                    <td>{record.record_type}</td>
                    <td className="code">{record.host}</td>
                    <td className="code">{record.value}</td>
                    <td>{record.verified ? <VerifiedBadge verified label="verified" /> : <VerifiedBadge verified={false} label={record.error || 'pending'} />}</td>
                    <td><CopyButton value={record.value} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {selectedRecords.some((record) => record.error) && <div className="notice warning">Fix the highlighted DNS errors, publish the records, then run Verify again.</div>}
          </section>
          <section className="card">
            <h2>Health score history</h2>
            <HealthBars compact data={history.map((item) => ({ label: new Date(item.recorded_at).toLocaleDateString(), value: item.health_score }))} />
            {history.length === 0 && <p className="muted">No history returned for this domain yet.</p>}
          </section>
        </div>
      )}
    </div>
  );
}
