/**
 * Full-screen boot/connection overlay shown while the backend is starting up.
 *
 * Keeps the main UI hidden until the backend health check resolves so the app
 * never flashes a "Failed to fetch" error during the normal startup window.
 * While connecting it shows a spinner; only after every retry has failed does
 * it surface an error with a manual retry action.
 */
export default function LoadingScreen({ state = 'connecting', error = null, onRetry }) {
  const offline = state === 'offline';
  return (
    <div className="boot-screen">
      <div className="boot-card">
        <div className="brand-mark boot-mark">PS</div>
        <h1 className="boot-title">PARIS SENDER</h1>
        {offline ? (
          <>
            <p className="boot-message boot-error">Unable to reach the backend service.</p>
            {error && <p className="muted boot-detail">{error}</p>}
            {onRetry && (
              <button type="button" className="primary" onClick={onRetry}>
                Retry connection
              </button>
            )}
          </>
        ) : (
          <>
            <div className="boot-spinner" aria-hidden="true" />
            <p className="boot-message">Connecting to PARIS SENDER backend...</p>
          </>
        )}
      </div>
    </div>
  );
}
