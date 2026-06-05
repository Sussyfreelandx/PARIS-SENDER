import { useEffect, useMemo, useRef, useState } from 'react';
import { autoVerifyDomain, createDomain, deleteDomain, diagnoseDomain, getDomain, getDomainHistory, getDomains, liveVerifyDomain, rotateDkim, verifyDomain } from '../api/client.js';
import { StatusBadge, VerifiedBadge } from '../components/Badge.jsx';
import CopyButton from '../components/CopyButton.jsx';
import HealthBars from '../components/HealthBars.jsx';

const initialForm = { name: '', spf_includes: '' };

// Provider-aware exponential backoff between DNS scan attempts. DNS records take
// time to propagate, so each retry waits longer than the last. The scan stops
// early the moment the domain is fully verified (DNS + provider).
const SCAN_BACKOFF_MS = [10000, 30000, 60000, 120000, 300000];
const SCAN_MAX_ATTEMPTS = SCAN_BACKOFF_MS.length + 1;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// A domain is only truly "verified" for sending when every DNS record passes AND
// the upstream email provider has independently verified it. DNS success alone
// must never be presented as a green/verified state.
const dnsValid = (domain) => Boolean(domain && domain.spf_verified && domain.dkim_verified && domain.dmarc_verified);
const fullyVerified = (domain) => dnsValid(domain) && Boolean(domain && domain.provider_verified);

export default function DomainManager() {
  const [domains, setDomains] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState(null);
  const [history, setHistory] = useState([]);
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [diagnosis, setDiagnosis] = useState(null);
  const [liveReport, setLiveReport] = useState(null);
  const [scan, setScan] = useState(null);
  const scanTokenRef = useRef(0);

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
    setHistory(historyResult.history || []);
  }

  useEffect(() => {
    loadDomains().catch((err) => setError(err.message));
    // Cancel any in-flight auto-scan when the page unmounts.
    return () => {
      scanTokenRef.current += 1;
    };
  }, []);

  useEffect(() => {
    setDiagnosis(null);
    setLiveReport(null);
    let cancelled = false;
    (async () => {
      try {
        // Selecting a domain only loads its current state. DNS scanning never
        // starts automatically — the operator must explicitly click
        // "Start verification" (and the provider gate must be satisfied first).
        await loadSelected(selectedId);
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  function stopScan() {
    scanTokenRef.current += 1;
    setScan(null);
  }

  // Run the DKIM/SPF/DMARC scan and poll with exponential backoff until the
  // domain is fully verified. The scan is gated on provider verification: DNS
  // can only be trusted once the upstream provider has confirmed the domain.
  async function runAutoScan(domainId) {
    if (!domainId) return;
    // Provider verification gate — block the scan when the provider has not yet
    // confirmed the domain.
    const current = selected && selected.id === domainId ? selected : await getDomain(domainId).catch(() => null);
    if (!current || !current.provider_verified) {
      setScan(null);
      setError('Domain must be verified with email provider before DNS scan');
      return;
    }
    const token = (scanTokenRef.current += 1);
    setError('');
    let result = null;
    for (let attempt = 1; attempt <= SCAN_MAX_ATTEMPTS; attempt += 1) {
      if (scanTokenRef.current !== token) return; // cancelled
      setScan({ domainId, attempt, maxAttempts: SCAN_MAX_ATTEMPTS, verified: false, message: `Scanning DNS for DKIM/SPF/DMARC (attempt ${attempt}/${SCAN_MAX_ATTEMPTS})…` });
      try {
        result = await autoVerifyDomain(domainId);
      } catch (err) {
        if (scanTokenRef.current !== token) return;
        setError(err.message);
        setScan(null);
        return;
      }
      if (scanTokenRef.current !== token) return;
      // Update only the selected domain's state — never refresh the full list
      // inside the scan loop.
      setSelected((curr) => (curr && curr.id === domainId ? result : curr));
      if (fullyVerified(result)) {
        setScan({ domainId, attempt, maxAttempts: SCAN_MAX_ATTEMPTS, verified: true, message: 'Domain verified — DNS and provider confirmed. Sending is enabled.' });
        return;
      }
      if (dnsValid(result)) {
        // DNS is correct but the provider has not confirmed sending yet; never
        // present this as a fully verified state.
        setScan({ domainId, attempt, maxAttempts: SCAN_MAX_ATTEMPTS, verified: false, message: 'DNS verified. Awaiting provider confirmation for sending activation.' });
        return;
      }
      if (attempt < SCAN_MAX_ATTEMPTS) {
        await sleep(SCAN_BACKOFF_MS[attempt - 1]);
      }
    }
    if (scanTokenRef.current !== token) return;
    setScan({ domainId, attempt: SCAN_MAX_ATTEMPTS, maxAttempts: SCAN_MAX_ATTEMPTS, verified: false, message: 'Records not found yet. They may still be propagating — publish them and the scan will pick them up, or run Diagnose DNS.' });
  }

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
      // DKIM selectors are provider-defined, not user-defined; the backend
      // assigns the selector automatically.
      const payload = { name: form.name, spf_includes: form.spf_includes.split(/[\s,]+/).filter(Boolean) };
      const created = await createDomain(payload);
      setForm(initialForm);
      setSelectedId(created.id);
      return created.id;
    });
    // Do NOT auto-start scanning. The domain stays in a "DNS setup pending"
    // state until the operator clicks "Start verification".
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
            </div>
            <div className="form-row inline">
              <div><label>SPF includes</label><input value={form.spf_includes} onChange={(event) => setForm({ ...form, spf_includes: event.target.value })} placeholder="include:_spf.example.com" /></div>
            </div>
            <p className="muted">The DKIM selector is assigned automatically by the provider, and the DMARC policy is detected from your DNS during the scan — no need to choose either.</p>
            <button className="primary" disabled={busy} type="submit">Add domain</button>
          </form>
        </section>

        <section className="card">
          <h2>Selected domain actions</h2>
          {selected ? (
            <>
              <div className="card-header"><strong>{selected.name}</strong><StatusBadge status={selected.status} /></div>
              {!selected.provider_verified && (
                <div className="notice warning" style={{ marginTop: 8 }}>
                  DNS setup pending — verify this domain with your email provider before starting the DNS scan.
                </div>
              )}
              <div className="actions">
                {scan && scan.domainId === selected.id && !scan.verified ? (
                  <button className="secondary" onClick={stopScan} type="button">Stop scan</button>
                ) : (
                  <button className="primary" onClick={() => runAutoScan(selected.id)} disabled={busy || !selected.provider_verified} title={!selected.provider_verified ? 'Domain must be verified with email provider before DNS scan' : undefined} type="button">Start verification</button>
                )}
                <button className="secondary" onClick={() => withBusy(async () => setSelected(await verifyDomain(selected.id)))} disabled={busy} type="button">Verify once</button>
                <button className="secondary" onClick={() => withBusy(async () => { const report = await diagnoseDomain(selected.id); setDiagnosis(report); await loadSelected(selected.id); })} disabled={busy} type="button">Diagnose DNS</button>
                <button className="secondary" onClick={() => withBusy(async () => { const report = await liveVerifyDomain(selected.id); setLiveReport(report); await loadSelected(selected.id); })} disabled={busy} type="button">Live DNS report</button>
                <button className="secondary" onClick={() => withBusy(async () => setSelected(await rotateDkim(selected.id)))} disabled={busy} type="button">Rotate DKIM</button>
                <button className="danger" onClick={() => { stopScan(); withBusy(async () => { await deleteDomain(selected.id); setSelectedId(''); setSelected(null); return ''; }); }} disabled={busy} type="button">Delete</button>
              </div>
              {scan && scan.domainId === selected.id && (
                <div className={scan.verified ? 'notice success' : 'notice'} style={{ marginTop: 12 }}>{scan.message}</div>
              )}

              <div className="grid two" style={{ marginTop: 16 }}>
                <div>
                  <h3>DNS status</h3>
                  <div className="actions" style={{ flexWrap: 'wrap' }}>
                    <VerifiedBadge verified={selected.spf_verified} label="SPF" />
                    <VerifiedBadge verified={selected.dkim_verified} label="DKIM" />
                    <VerifiedBadge verified={selected.dmarc_verified} label="DMARC" />
                  </div>
                </div>
                <div>
                  <h3>Provider status</h3>
                  <div className="actions" style={{ flexWrap: 'wrap' }}>
                    <VerifiedBadge verified={selected.provider_verified} label={selected.provider_verified ? 'provider verified' : 'provider not verified'} />
                    <span className="muted">Provider: <strong>{selected.provider_name || 'unknown'}</strong></span>
                    <VerifiedBadge verified={selected.sending_enabled} label={selected.sending_enabled ? 'sending enabled' : 'sending disabled'} />
                  </div>
                </div>
              </div>
              <div className={selected.sending_enabled ? 'notice success' : 'notice'} style={{ marginTop: 12 }}>
                {selected.sending_enabled
                  ? 'DNS and provider both verified — Send Campaign is enabled for this domain.'
                  : (dnsValid(selected)
                    ? 'DNS verified. Awaiting provider confirmation for sending activation.'
                    : 'Send Campaign stays disabled until both DNS and provider verification pass.')}
              </div>

              <div className="form-row" style={{ marginTop: 16 }}>
                <label>DKIM selector (provider-assigned)</label>
                <div className="actions">
                  <span className="code">{selected.dkim_selector || 'pending'}</span>
                  <span className="muted">DKIM selectors are provider-defined and cannot be edited.</span>
                </div>
              </div>
              <div className="form-row" style={{ marginTop: 16 }}>
                <label>DMARC policy (auto-detected)</label>
                <div className="actions">
                  <StatusBadge status={selected.dmarc_verified ? 'VERIFIED' : 'PENDING'} />
                  <span className="code">p={selected.dmarc_policy || 'none'}</span>
                  <span className="muted">{selected.dmarc_verified ? 'Read from your published DMARC record.' : 'Will be detected from DNS once your DMARC record is found.'}</span>
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

      {selected && liveReport && liveReport.domain === selected.name && (
        <section className="card">
          <div className="card-header">
            <h2>Live DNS verification</h2>
            <button className="ghost small" onClick={() => setLiveReport(null)} type="button">Dismiss</button>
          </div>
          <p className="muted">
            Source: <strong>{liveReport.verification_source}</strong> · Provider: <strong>{liveReport.provider_detected || 'Unknown'}</strong> · Checked {liveReport.verification_timestamp}
          </p>
          <div className="actions" style={{ flexWrap: 'wrap' }}>
            <VerifiedBadge verified={liveReport.dns_resolves} label="resolves" />
            <VerifiedBadge verified={liveReport.mx_present} label="MX" />
            <VerifiedBadge verified={liveReport.spf_valid} label="SPF" />
            <VerifiedBadge verified={liveReport.dkim_valid} label="DKIM" />
            <VerifiedBadge verified={liveReport.dmarc_valid} label="DMARC" />
          </div>
          {Object.keys(liveReport.errors || {}).length > 0 ? (
            <div className="notice warning" style={{ marginTop: 12 }}>
              <ul className="send-reasons">
                {Object.entries(liveReport.errors).map(([key, message]) => <li key={key}>{message}</li>)}
              </ul>
            </div>
          ) : (
            <div className="notice success" style={{ marginTop: 12 }}>All authentication records verified against live DNS.</div>
          )}
        </section>
      )}

      {selected && diagnosis && (
        <section className="card">
          <div className="card-header">
            <h2>DNS diagnosis</h2>
            <button className="ghost small" onClick={() => setDiagnosis(null)} type="button">Dismiss</button>
          </div>
          <p className="muted">
            Detected provider: <strong>{diagnosis.provider?.provider || 'Unknown'}</strong>
            {diagnosis.provider?.nameservers?.length ? ` (${diagnosis.provider.nameservers.join(', ')})` : ''}
          </p>
          {diagnosis.provider?.guidance && <div className="notice">{diagnosis.provider.guidance}</div>}
          <div className={diagnosis.failing_count === 0 ? 'notice success' : 'notice warning'}>{diagnosis.summary}</div>
          <table className="table">
            <thead><tr><th>Type</th><th>Host</th><th>Status</th><th>Diagnosis</th></tr></thead>
            <tbody>
              {(diagnosis.records || []).map((record) => (
                <tr key={`diag-${record.record_type}-${record.host}`}>
                  <td>{record.record_type}</td>
                  <td className="code">{record.host}</td>
                  <td>{record.verified ? <VerifiedBadge verified label="ok" /> : <VerifiedBadge verified={false} label="failing" />}</td>
                  <td>{record.verified ? <span className="muted">Published correctly.</span> : <span>{record.hint || record.error}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
