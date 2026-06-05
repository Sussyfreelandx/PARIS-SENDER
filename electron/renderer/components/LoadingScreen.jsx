/**
 * Full-screen boot/connection overlay shown while the backend is starting up.
 *
 * Keeps the main UI hidden until the backend health check resolves so the app
 * never flashes a "Failed to fetch" error during the normal startup window.
 * While connecting it shows a spinner; if the handshake exceeds the hint delay
 * (or fails outright) it surfaces classified diagnostic hints so the operator
 * is never left staring at an indefinite "connecting" screen.
 *
 * @param {{
 *   state?: string,
 *   error?: string|null,
 *   classification?: { kind: string, message: string }|null,
 *   hints?: { key: string, title: string, detail: string }[],
 *   showHints?: boolean,
 *   onRetry?: () => void
 * }} props
 */
export default function LoadingScreen({
  state = 'connecting',
  error = null,
  classification = null,
  hints = [],
  showHints = false,
  onRetry
}) {
  const offline = state === 'offline';
  const renderHints = (offline || showHints) && hints.length > 0;

  return (
    <div className="boot-screen">
      <div className="boot-card">
        <div className="brand-mark boot-mark">PS</div>
        <h1 className="boot-title">PARIS SENDER</h1>

        {offline ? (
          <p className="boot-message boot-error">Unable to reach the backend service.</p>
        ) : (
          <>
            <div className="boot-spinner" aria-hidden="true" />
            <p className="boot-message">Connecting to PARIS SENDER backend…</p>
            {showHints && (
              <p className="muted boot-detail">
                The backend is taking longer than expected to respond.
              </p>
            )}
          </>
        )}

        {(error || classification?.message) && (
          <p className="muted boot-detail">{error || classification?.message}</p>
        )}

        {renderHints && (
          <div className="boot-hints">
            <p className="boot-hints-title">Troubleshooting</p>
            <ul>
              {hints.map((hint) => (
                <li key={hint.key}>
                  <strong>{hint.title}</strong>
                  <span>{hint.detail}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {offline && onRetry && (
          <button type="button" className="primary" onClick={onRetry}>
            Retry connection
          </button>
        )}
      </div>
    </div>
  );
}
