"""Robust, freeze-safe production entrypoint for the Paris Sender backend.

This module is the single launch target for the packaged desktop application.
It is intentionally defensive so that a packaged executable never exits silently
with "no visible error":

* The project root is placed on ``sys.path`` so absolute ``backend.*`` imports
  resolve whether the process is run from source, as a plain script, or frozen
  by PyInstaller (where the script directory — not the repo root — would
  otherwise be first on ``sys.path``).
* The whole startup sequence is wrapped in ``try/except`` and any failure (import
  error, dependency error, path error, configuration error, or hidden exception
  raised while building the FastAPI app) is written with a full stack trace to
  ``logs/startup.log`` before the process exits.

Configuration is read from the same environment variables used by
``backend.server`` so the Electron main process can pick a free port and pass it
through:

* ``PARIS_HOST`` – interface to bind (default ``127.0.0.1``).
* ``PARIS_PORT`` – TCP port to bind (default ``8000``).
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _project_root() -> Path:
    """Return the directory that should be importable as the project root.

    When frozen by PyInstaller the bundled modules live under ``sys._MEIPASS``;
    otherwise the repository root is the parent of the ``backend`` package.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def _log_dir() -> Path:
    """Return (and create) the directory that holds ``startup.log``.

    For a frozen executable logs are written next to the binary so users can find
    them; from source they go to the repository ``logs/`` directory.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    directory = base / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_startup_log(message: str) -> None:
    """Append a timestamped message to ``logs/startup.log`` (best effort)."""
    try:
        path = _log_dir() / "startup.log"
        timestamp = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:  # noqa: BLE001 - logging must never crash startup
        pass


def run() -> None:
    """Initialise and run the backend, capturing every startup failure.

    Any exception raised while importing the application, resolving
    configuration, or starting uvicorn is logged with a full stack trace to
    ``logs/startup.log`` and then re-raised so a supervising process (Electron)
    or an attached console still surfaces the error.
    """
    root = str(_project_root())
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        _write_startup_log("Paris Sender backend starting")

        # Imported lazily inside the try block so import/configuration errors are
        # captured in the startup log instead of vanishing silently.
        import uvicorn

        from backend.server import app, _resolve_host, _resolve_port

        host = _resolve_host()
        port = _resolve_port()
        _write_startup_log(f"Backend listening on http://{host}:{port}")

        uvicorn.run(app, host=host, port=port, log_level="info")

        _write_startup_log("Backend stopped cleanly")
    except Exception:  # noqa: BLE001 - capture ALL startup failures, never swallow
        _write_startup_log("FATAL startup failure:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    import multiprocessing

    # Required so frozen executables do not re-spawn the app when a dependency
    # uses multiprocessing internally.
    multiprocessing.freeze_support()
    run()
