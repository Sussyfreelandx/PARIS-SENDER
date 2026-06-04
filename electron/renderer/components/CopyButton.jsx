import { useState } from 'react';

export default function CopyButton({ value }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(value || '');
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <button className="ghost small" onClick={copy} type="button" disabled={!value}>
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}
