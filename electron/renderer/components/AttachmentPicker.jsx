import { useEffect, useState } from 'react';
import { fileToAttachment, readAttachments, subscribeAttachments, writeAttachments } from '../api/attachments.js';

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Shared attachment picker used in the compose area. Files are read in the
// browser, base64-encoded, and stored in the shared attachment store so the
// campaign send screen includes them in the outgoing message.
export default function AttachmentPicker() {
  const [attachments, setAttachments] = useState(readAttachments);
  const [error, setError] = useState('');

  useEffect(() => subscribeAttachments(setAttachments), []);

  async function onFiles(event) {
    setError('');
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (files.length === 0) return;
    try {
      const added = await Promise.all(files.map(fileToAttachment));
      const existing = readAttachments();
      const byName = new Map(existing.map((item) => [item.filename, item]));
      added.forEach((item) => byName.set(item.filename, item));
      writeAttachments(Array.from(byName.values()));
    } catch (err) {
      setError(err.message || 'Failed to attach file');
    }
  }

  function remove(filename) {
    writeAttachments(readAttachments().filter((item) => item.filename !== filename));
  }

  function clearAll() {
    writeAttachments([]);
  }

  return (
    <div className="attachment-picker">
      <div className="card-header">
        <h3>Attachments</h3>
        {attachments.length > 0 && (
          <button className="ghost small" type="button" onClick={clearAll}>Clear all</button>
        )}
      </div>
      <p className="muted">Files attached here are included on every outgoing email in the campaign send.</p>
      <label className="file-drop">
        <input type="file" multiple onChange={onFiles} />
        <span className="file-drop-cta">Choose files…</span>
        <span className="muted">or drag &amp; drop here</span>
      </label>
      {error && <div className="notice danger">{error}</div>}
      <div className="list">
        {attachments.length === 0 && <p className="muted">No attachments added.</p>}
        {attachments.map((item) => (
          <div className="list-item" key={item.filename}>
            <div>
              <strong>{item.filename}</strong>
              <p className="muted">{item.mime_type} {item.size ? `· ${formatSize(item.size)}` : ''}</p>
            </div>
            <button className="ghost small" type="button" onClick={() => remove(item.filename)}>Remove</button>
          </div>
        ))}
      </div>
    </div>
  );
}
