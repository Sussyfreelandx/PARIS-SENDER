import { useEffect, useState } from 'react';

/**
 * Surfaces live auto-update status emitted by the Electron main process.
 *
 * It only renders when there is something actionable to show (an update is
 * available, downloading, ready to install, or the last check errored). All
 * values come from real electron-updater events via the preload bridge — never
 * fabricated. In the browser/dev build (no parisAPI.updates) it renders nothing.
 *
 * Reactivity: when a `status` prop is supplied (from the centralized
 * `useUpdateStatus` hook) the banner reflects update-state changes immediately.
 * When no prop is given it falls back to subscribing to the bridge directly so
 * it remains usable in isolation.
 *
 * @param {{ status?: object|null, onInstall?: () => Promise<any> }} props
 */
export default function UpdateBanner({ status: statusProp, onInstall } = {}) {
  const updates = typeof window !== 'undefined' ? window.parisAPI?.updates : null;
  const controlled = statusProp !== undefined;
  const [localStatus, setLocalStatus] = useState(null);
  const [installing, setInstalling] = useState(false);

  useEffect(() => {
    if (controlled || !updates) return undefined;
    let active = true;
    updates.getStatus?.().then((s) => {
      if (active) setLocalStatus(s);
    }).catch(() => {});
    const unsubscribe = updates.onStatus?.((s) => {
      if (active) setLocalStatus(s);
    });
    return () => {
      active = false;
      if (typeof unsubscribe === 'function') unsubscribe();
    };
  }, [updates, controlled]);

  const status = controlled ? statusProp : localStatus;

  if (!updates || !status) return null;

  const { state } = status;
  if (!['available', 'downloading', 'downloaded', 'error'].includes(state)) {
    return null;
  }

  async function handleInstall() {
    setInstalling(true);
    try {
      if (onInstall) {
        await onInstall();
      } else {
        await updates.install();
      }
    } catch {
      setInstalling(false);
    }
  }

  if (state === 'error') {
    return (
      <div className="notice danger update-banner">
        Update check failed: {status.error || 'unknown error'}.
      </div>
    );
  }

  if (state === 'available') {
    return (
      <div className="notice update-banner">
        Update {status.version ? `v${status.version}` : ''} available — downloading in the background…
      </div>
    );
  }

  if (state === 'downloading') {
    return (
      <div className="notice update-banner">
        Downloading update {status.version ? `v${status.version}` : ''}: {status.percent ?? 0}%
      </div>
    );
  }

  // downloaded
  return (
    <div className="notice success update-banner">
      <div>
        <strong>Update {status.version ? `v${status.version}` : ''} ready to install.</strong>
        {status.releaseNotes && (
          <details className="update-notes">
            <summary>Release notes</summary>
            <pre>{status.releaseNotes}</pre>
          </details>
        )}
      </div>
      <button type="button" className="primary" onClick={handleInstall} disabled={installing}>
        {installing ? 'Restarting…' : 'Restart & install'}
      </button>
    </div>
  );
}
