// Shared attachment store backed by localStorage so the compose area and the
// campaign send screen operate on the same set of files. Attachments are stored
// as { filename, content_base64, mime_type, size } — exactly the shape the
// backend /campaigns/{id}/send endpoint expects under `attachments`.

const ATTACHMENT_KEY = 'paris_sender_attachments';
const listeners = new Set();

export function readAttachments() {
  try {
    const parsed = JSON.parse(localStorage.getItem(ATTACHMENT_KEY) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function writeAttachments(attachments) {
  const list = Array.isArray(attachments) ? attachments : [];
  localStorage.setItem(ATTACHMENT_KEY, JSON.stringify(list));
  listeners.forEach((listener) => listener(list));
  return list;
}

export function subscribeAttachments(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Convert the payload kept in the store into the array the API expects.
export function toApiAttachments(attachments) {
  return (attachments || []).map(({ filename, content_base64, mime_type }) => ({
    filename,
    content_base64,
    mime_type: mime_type || 'application/octet-stream'
  }));
}

// Read a File object into the stored attachment shape (base64 without the data
// URL prefix, which is what the backend decodes).
export function fileToAttachment(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error('Failed to read file'));
    reader.onload = () => {
      const result = String(reader.result || '');
      const base64 = result.includes(',') ? result.slice(result.indexOf(',') + 1) : result;
      resolve({
        filename: file.name,
        content_base64: base64,
        mime_type: file.type || 'application/octet-stream',
        size: file.size
      });
    };
    reader.readAsDataURL(file);
  });
}
