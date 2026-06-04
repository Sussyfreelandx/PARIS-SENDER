import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import { LogProvider } from './components/LogContext.jsx';
import './styles.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <LogProvider>
      <App />
    </LogProvider>
  </React.StrictMode>
);
