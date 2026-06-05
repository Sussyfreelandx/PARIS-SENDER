import { useCallback, useEffect, useState } from 'react';

const ACTIVE_STATES = ['available', 'downloading', 'downloaded'];

/**
 * Centralized, reactive auto-update status.
 *
 * Subscribes once to the Electron main process update events (via the preload
 * `parisAPI.updates` bridge) and exposes a normalized view that ties the app
 * version display to the update system. Every value is sourced from real
 * electron-updater events — nothing is fabricated. When the bridge is absent
 * (browser/dev build) it degrades to an explicit "unsupported" state.
 *
 * @returns {{
 *   available: boolean,
 *   status: object|null,
 *   state: string,
 *   current_version: string|null,
 *   latest_version: string|null,
 *   update_available: boolean,
 *   update_channel: string|null,
 *   error: string|null,
 *   check: (() => Promise<any>)|null,
 *   install: (() => Promise<any>)|null
 * }}
 */
export default function useUpdateStatus() {
  const updates = typeof window !== 'undefined' ? window.parisAPI?.updates : null;
  const currentVersion = (typeof window !== 'undefined' && window.parisAPI?.appVersion) || null;
  const [status, setStatus] = useState(null);
  const [channel, setChannel] = useState(null);

  useEffect(() => {
    if (!updates) return undefined;
    let active = true;
    // Replay the last known status immediately, then stay subscribed so the UI
    // reacts the instant the update state changes.
    updates.getStatus?.().then((s) => active && setStatus(s)).catch(() => {});
    updates.getChannel?.().then((c) => active && setChannel(c)).catch(() => {});
    const unsubscribe = updates.onStatus?.((s) => active && setStatus(s));
    return () => {
      active = false;
      if (typeof unsubscribe === 'function') unsubscribe();
    };
  }, [updates]);

  const check = useCallback(async () => {
    if (!updates?.check) return null;
    const result = await updates.check();
    if (result) setStatus(result);
    return result;
  }, [updates]);

  const install = useCallback(() => {
    if (!updates?.install) return Promise.resolve(null);
    return updates.install();
  }, [updates]);

  const state = status?.state || (updates ? 'idle' : 'unsupported');
  const updateAvailable = ACTIVE_STATES.includes(state);

  return {
    available: Boolean(updates),
    status,
    state,
    current_version: currentVersion,
    latest_version: updateAvailable ? status?.version ?? null : null,
    update_available: updateAvailable,
    update_channel: channel || (updates ? 'stable' : null),
    error: state === 'error' ? status?.error || 'unknown error' : null,
    check: updates ? check : null,
    install: updates ? install : null
  };
}
