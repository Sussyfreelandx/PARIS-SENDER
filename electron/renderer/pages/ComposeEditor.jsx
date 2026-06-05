import { useEffect, useMemo, useState } from 'react';
import { analyzeCompose, previewCompose } from '../api/client.js';
import Badge from '../components/Badge.jsx';
import AttachmentPicker from '../components/AttachmentPicker.jsx';

const SETTINGS_KEY = 'paris_sender_settings';

function readNonSmtpDefault() {
  try { return Boolean(JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}').nonSmtpDefault); } catch { return false; }
}

const defaultTemplate = 'Hello [firstname],\n\nHere is the latest update from PARIS SENDER.';

export default function ComposeEditor() {
  const [html, setHtml] = useState(false);
  const [template, setTemplate] = useState(defaultTemplate);
  const [previewEmail, setPreviewEmail] = useState('');
  const [preview, setPreview] = useState({ rendered: defaultTemplate, context: {} });
  const [analysis, setAnalysis] = useState(null);
  const [error, setError] = useState('');
  const [nonSmtpDelivery, setNonSmtpDelivery] = useState(readNonSmtpDefault);

  useEffect(() => {
    const settings = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...settings, nonSmtpDefault: nonSmtpDelivery }));
  }, [nonSmtpDelivery]);

  useEffect(() => {
    const timer = window.setTimeout(async () => {
      setError('');
      try {
        const [previewResult, analysisResult] = await Promise.all([
          previewCompose({ template, email: previewEmail, html }),
          analyzeCompose({ content: template, html })
        ]);
        setPreview(previewResult);
        setAnalysis(analysisResult);
      } catch (err) {
        setError(err.message);
      }
    }, 450);
    return () => window.clearTimeout(timer);
  }, [template, previewEmail, html]);

  const ratio = Math.round(Number(analysis?.html_text_ratio || 0) * 100);
  const warnings = useMemo(() => [
    ...(analysis?.warnings || []),
    ...(analysis?.unknown_placeholders || []).map((item) => `Unknown placeholder: ${item}`),
    ...(analysis?.jinja_errors || []).map((item) => `Jinja: ${item}`)
  ], [analysis]);

  const rendered = preview.rendered || template;

  return (
    <div className="grid two">
      <section className="card">
        <div className="card-header">
          <h2>Compose</h2>
          <div className="actions">
            <label className="switch"><input type="checkbox" checked={html} onChange={(event) => setHtml(event.target.checked)} /> HTML mode</label>
            <label className="switch"><input type="checkbox" checked={nonSmtpDelivery} onChange={(event) => setNonSmtpDelivery(event.target.checked)} /> Non-SMTP delivery</label>
          </div>
        </div>
        <div className="form-row">
          <label>Recipient email for personalization preview</label>
          <input value={previewEmail} onChange={(event) => setPreviewEmail(event.target.value)} placeholder="recipient@example.com" />
        </div>
        <div className="form-row">
          <label>{html ? 'HTML template' : 'Plain text template'}</label>
          <textarea value={template} onChange={(event) => setTemplate(event.target.value)} style={{ minHeight: 360 }} />
        </div>
        {error && <div className="notice danger">{error}</div>}
      </section>

      <section className="card">
        <h2>Live personalized preview</h2>
        {html ? (
          <iframe className="preview-frame" title="HTML preview" sandbox="" srcDoc={rendered} />
        ) : (
          <pre className="preview-pre code">{rendered}</pre>
        )}
        <h3>Resolved context</h3>
        <pre className="code">{JSON.stringify(preview.context || {}, null, 2)}</pre>
      </section>

      <section className="card">
        <h2>Validation</h2>
        <div className="grid three">
          <div><p className="eyebrow">Characters</p><p className="metric">{analysis?.char_count ?? template.length}</p></div>
          <div><p className="eyebrow">Text chars</p><p className="metric">{analysis?.text_char_count ?? template.length}</p></div>
          <div><p className="eyebrow">Valid</p><Badge tone={analysis?.valid ? 'success' : 'warning'}>{analysis?.valid ? 'Yes' : 'Review'}</Badge></div>
        </div>
        <div className="form-row" style={{ marginTop: 18 }}>
          <label>HTML/Text ratio: {ratio}%</label>
          <div className="meter"><span style={{ width: `${Math.min(100, ratio)}%` }} /></div>
        </div>
        <h3>Warnings</h3>
        <div className="list">
          {warnings.length === 0 && <p className="muted">No placeholder, Jinja, or validation warnings.</p>}
          {warnings.map((warning) => <div className="list-item" key={warning}>{warning}</div>)}
        </div>
      </section>

      <section className="card">
        <h2>Spam indicators</h2>
        <p className="metric">{analysis?.spam_score ?? 0}</p>
        <p className="muted">Spam score</p>
        <div className="actions">
          {(analysis?.spam_words || []).map((word) => <Badge tone="warning" key={word}>{word}</Badge>)}
          {(analysis?.spam_words || []).length === 0 && <span className="muted">No spam words detected.</span>}
        </div>
        <h3>Placeholders</h3>
        <div className="actions">
          {(analysis?.placeholders || []).map((placeholder) => <Badge key={placeholder}>{placeholder}</Badge>)}
        </div>
      </section>

      <section className="card">
        <AttachmentPicker />
      </section>
    </div>
  );
}
