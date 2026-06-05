import { useCallback, useEffect, useRef, useState } from 'react';
import { getDiagnostics, probeHealth } from '../api/client.js';

const HANDSHAKE_INTERVAL_MS = 1000; // delay between startup probes
const HANDSHAKE_WINDOW_MS = 30000; // give the backend up to 30s to boot
const HINT_DELAY_MS = 15000; // surface diagnostic hints if still connecting after 15s
const POLL_INTERVAL_MS = 30000; // steady-state health polling cadence
const DEGRADE_AFTER = 3; // consecutive failures before healthy -> degraded
const OFFLINE_AFTER = 6; // consecutive failures before degraded -> offline

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const INITIAL_BACKEND = {
  status: 'starting',
  classification: null,
  error: null,
  version: null,
  lastOkAt: null,
  consecutiveFailures: 0
};

/**
 * Orchestrates the backend connection lifecycle as a resilient, observable,
 * non-binary state machine:
 *
 *   starting -> connecting -> healthy -> degraded -> offline (with recovery)
 *
 * Responsibilities:
 *  - Startup handshake: poll /health, classifying boot delay vs crash vs
 *    timeout, and never block the UI indefinitely (diagnostic hints after 15s).
 *  - Steady-state polling every 30s that does NOT overwrite a healthier state
 *    with a single transient failure — it only downgrades after 3 consecutive
 *    failures, and escalates to offline after sustained failure.
 *  - Automatic recovery: a successful probe restores "healthy".
 *  - Refreshes the /diagnostics snapshot (database + delivery) alongside health
 *    so every failure is observable, never silently swallowed.
 */
export default function useBackendLifecycle() {
  const [phase, setPhase] = useState('starting');
  const [backend, setBackend] = useState(INITIAL_BACKEND);
  const [diagnostics, setDiagnostics] = useState(null);
  const [showHints, setShowHints] = useState(false);
  const [monitoring, setMonitoring] = useState(false);

  const cancelledRef = useRef(false);
  const failuresRef = useRef(0);
  const lastOkRef = useRef(null);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  const refreshDiagnostics = useCallback(async () => {
    try {
      const diag = await getDiagnostics();
      if (!cancelledRef.current) setDiagnostics(diag);
    } catch (error) {
      // Surface, never swallow: keep prior snapshot but record the probe error.
      if (!cancelledRef.current) {
        setDiagnostics((prev) => ({ ...(prev || {}), _error: error.message }));
      }
    }
  }, []);

  const markHealthy = useCallback((version) => {
    failuresRef.current = 0;
    lastOkRef.current = new Date().toISOString();
    setBackend({
      status: 'healthy',
      classification: null,
      error: null,
      version: version ?? null,
      lastOkAt: lastOkRef.current,
      consecutiveFailures: 0
    });
    setShowHints(false);
    setPhase('healthy');
    setMonitoring(true);
    refreshDiagnostics();
  }, [refreshDiagnostics]);

  // Startup handshake: retry /health silently until it answers or the boot
  // window elapses. The main UI stays hidden behind the connecting screen.
  const connect = useCallback(async () => {
    cancelledRef.current = false;
    failuresRef.current = 0;
    setShowHints(false);
    setPhase('connecting');
    setBackend((b) => ({ ...b, status: 'connecting', classification: null, error: null }));

    const startedAt = Date.now();
    const hintTimer = setTimeout(() => {
      if (!cancelledRef.current) setShowHints(true);
    }, HINT_DELAY_MS);

    let lastClassification = null;
    try {
      while (!cancelledRef.current && Date.now() - startedAt < HANDSHAKE_WINDOW_MS) {
        const result = await probeHealth();
        if (cancelledRef.current) return;
        if (result.ok) {
          markHealthy(result.version);
          return;
        }
        lastClassification = result.classification || null;
        setBackend((b) => ({
          ...b,
          status: 'connecting',
          classification: lastClassification,
          error: lastClassification?.message ?? null
        }));
        await delay(HANDSHAKE_INTERVAL_MS);
      }
    } finally {
      clearTimeout(hintTimer);
    }

    if (cancelledRef.current) return;
    // Boot window exhausted without a successful probe.
    setShowHints(true);
    setBackend((b) => ({
      ...b,
      status: 'offline',
      classification: lastClassification,
      error: lastClassification?.message ?? 'Backend is unreachable.'
    }));
    setPhase('offline');
  }, [markHealthy]);

  useEffect(() => {
    connect();
    return () => {
      cancelledRef.current = true;
    };
  }, [connect]);

  // Steady-state polling, active once the initial handshake has succeeded at
  // least once. Transient failures degrade gradually and recover automatically.
  useEffect(() => {
    if (!monitoring) return undefined;
    let cancelled = false;

    async function poll() {
      const result = await probeHealth();
      if (cancelled) return;
      if (result.ok) {
        // Recovery: a healthy response always restores "healthy".
        failuresRef.current = 0;
        lastOkRef.current = new Date().toISOString();
        setBackend({
          status: 'healthy',
          classification: null,
          error: null,
          version: result.version ?? null,
          lastOkAt: lastOkRef.current,
          consecutiveFailures: 0
        });
        setPhase('healthy');
        refreshDiagnostics();
        return;
      }

      const failures = (failuresRef.current += 1);
      // Do not overwrite a healthier state on a single transient failure.
      let nextStatus;
      if (failures >= OFFLINE_AFTER) {
        nextStatus = 'offline';
      } else if (failures >= DEGRADE_AFTER) {
        nextStatus = 'degraded';
      } else {
        nextStatus = phaseRef.current === 'offline' ? 'offline' : 'healthy';
      }
      setBackend((b) => ({
        ...b,
        status: nextStatus,
        classification: result.classification || null,
        error: result.classification?.message ?? 'Backend health check failed.',
        lastOkAt: lastOkRef.current,
        consecutiveFailures: failures
      }));
      setPhase(nextStatus);
    }

    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [monitoring, refreshDiagnostics]);

  return {
    phase,
    backend,
    diagnostics,
    showHints,
    /** True once the backend has connected at least once this session. */
    everConnected: monitoring,
    retry: connect
  };
}
