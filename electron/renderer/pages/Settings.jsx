import { useState } from 'react';
import { BASE_URL, testSmtp } from '../api/client.js';

const SETTINGS_KEY = 'paris_sender_settings';

const DEFAULT_SMTP = { host: '', port: 587, username: '', password: '', use_tls: true, use_ssl: false };
const DEFAULT_NON_SMTP = { port: 25, helo: '' };

function readSettings() {
  const base = {
    senderName: 'PARIS SENDER',
    nonSmtpDefault: false,
    smtp: { ...DEFAULT_SMTP },
    nonSmtp: { ...DEFAULT_NON_SMTP }
  };
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
    return {
      ...base,
      ...stored,
      smtp: { ...DEFAULT_SMTP, ...(stored.smtp || {}) },
      nonSmtp: { ...DEFAULT_NON_SMTP, ...(stored.nonSmtp || {}) }
    };
  } catch {
    return base;
  }
}

export default function Settings() {
  const [settings, setSettings] = useState(readSettings);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const setSmtp = (patch) => setSettings((prev) => ({ ...prev, smtp: { ...prev.smtp, ...patch } }));
  const setNonSmtp = (patch) => setSettings((prev) => ({ ...prev, nonSmtp: { ...prev.nonSmtp, ...patch } }));

  // Persist explicitly so the user gets clear "Saved" feedback instead of a
  // silent background write.
  function saveSettings() {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2500);
  }

  // Validate the SMTP credentials against the backend without sending mail.
  async function testConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testSmtp({
        host: settings.smtp.host,
        port: Number(settings.smtp.port),
        username: settings.smtp.username || null,
        password: settings.smtp.password || null,
        use_tls: settings.smtp.use_tls,
        use_ssl: settings.smtp.use_ssl
      });
      setTestResult(result);
      // A successful test is a good moment to persist the working config.
      if (result.success) localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch (err) {
      setTestResult({ success: false, detail: err.message });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="grid two">
      <section className="card">
        <h2>Backend</h2>
        <div className="form-row">
          <label>Backend URL</label>
          <input value={BASE_URL} readOnly />
        </div>
        <p className="muted">The backend URL is exposed by the preload bridge and defaults to FastAPI on port 8000.</p>
      </section>
      <section className="card">
        <h2>Sender defaults</h2>
        <div className="form-row">
          <label>Default sender name</label>
          <input value={settings.senderName} onChange={(event) => setSettings({ ...settings, senderName: event.target.value })} />
        </div>
        <label className="switch">
          <input type="checkbox" checked={settings.nonSmtpDefault} onChange={(event) => setSettings({ ...settings, nonSmtpDefault: event.target.checked })} />
          Use non-SMTP delivery by default
        </label>
      </section>

      <section className="card">
        <h2>SMTP configuration</h2>
        <p className="muted">Used when a campaign is sent over SMTP. Credentials are kept locally and sent to the backend only with each send request.</p>
        <div className="form-row">
          <label>SMTP host</label>
          <input value={settings.smtp.host} onChange={(event) => setSmtp({ host: event.target.value })} placeholder="smtp.example.com" />
        </div>
        <div className="grid two">
          <div className="form-row">
            <label>Port</label>
            <input type="number" value={settings.smtp.port} onChange={(event) => setSmtp({ port: Number(event.target.value) })} />
          </div>
          <div className="form-row">
            <label>Username</label>
            <input value={settings.smtp.username} onChange={(event) => setSmtp({ username: event.target.value })} placeholder="user@example.com" />
          </div>
        </div>
        <div className="form-row">
          <label>Password</label>
          <input type="password" value={settings.smtp.password} onChange={(event) => setSmtp({ password: event.target.value })} autoComplete="new-password" />
        </div>
        <div className="actions">
          <label className="switch">
            <input type="checkbox" checked={settings.smtp.use_tls} onChange={(event) => setSmtp({ use_tls: event.target.checked })} />
            STARTTLS
          </label>
          <label className="switch">
            <input type="checkbox" checked={settings.smtp.use_ssl} onChange={(event) => setSmtp({ use_ssl: event.target.checked })} />
            SSL/TLS
          </label>
        </div>
        <div className="actions" style={{ marginTop: 14 }}>
          <button className="primary" type="button" onClick={saveSettings}>Save configuration</button>
          <button className="secondary" type="button" onClick={testConnection} disabled={testing || !settings.smtp.host}>
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          {saved && <span className="muted">Saved.</span>}
        </div>
        {testResult && (
          <div className={testResult.success ? 'notice success' : 'notice danger'} style={{ marginTop: 12 }}>
            {testResult.detail || (testResult.success ? 'Connected successfully.' : 'Connection failed.')}
          </div>
        )}
      </section>

      <section className="card">
        <h2>Non-SMTP (direct delivery)</h2>
        <p className="muted">When sending without SMTP, PARIS connects directly to each recipient's mail exchanger.</p>
        <div className="grid two">
          <div className="form-row">
            <label>Delivery port</label>
            <input type="number" value={settings.nonSmtp.port} onChange={(event) => setNonSmtp({ port: Number(event.target.value) })} />
          </div>
          <div className="form-row">
            <label>HELO/EHLO hostname</label>
            <input value={settings.nonSmtp.helo} onChange={(event) => setNonSmtp({ helo: event.target.value })} placeholder="mail.yourdomain.com" />
          </div>
        </div>
        <div className="actions" style={{ marginTop: 14 }}>
          <button className="primary" type="button" onClick={saveSettings}>Save configuration</button>
          {saved && <span className="muted">Saved.</span>}
        </div>
      </section>
    </div>
  );
}
