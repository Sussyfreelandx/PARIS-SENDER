import { useEffect, useState } from 'react';
import { BASE_URL } from '../api/client.js';

const SETTINGS_KEY = 'paris_sender_settings';

function readSettings() {
  try {
    return { senderName: 'Paris Sender', nonSmtpDefault: false, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}') };
  } catch {
    return { senderName: 'Paris Sender', nonSmtpDefault: false };
  }
}

export default function Settings() {
  const [settings, setSettings] = useState(readSettings);

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }, [settings]);

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
    </div>
  );
}
