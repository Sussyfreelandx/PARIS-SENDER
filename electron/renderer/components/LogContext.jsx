import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { subscribeApiLogs } from '../api/client.js';

const LogContext = createContext(null);
const STORAGE_KEY = 'paris_sender_logs';

function readStoredLogs() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
  } catch {
    return [];
  }
}

export function LogProvider({ children }) {
  const [logs, setLogs] = useState(readStoredLogs);

  useEffect(() => {
    const unsubscribe = subscribeApiLogs((entry) => {
      setLogs((current) => [entry, ...current].slice(0, 300));
    });
    return unsubscribe;
  }, []);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(logs));
  }, [logs]);

  const value = useMemo(() => ({
    logs,
    addLog(entry) {
      setLogs((current) => [{ timestamp: new Date().toISOString(), ...entry }, ...current].slice(0, 300));
    },
    clearLogs() {
      setLogs([]);
    }
  }), [logs]);

  return <LogContext.Provider value={value}>{children}</LogContext.Provider>;
}

export function useLogs() {
  const context = useContext(LogContext);
  if (!context) {
    throw new Error('useLogs must be used within LogProvider');
  }
  return context;
}
