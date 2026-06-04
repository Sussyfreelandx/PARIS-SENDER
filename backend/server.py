"""Production server entrypoint for the Paris Sender desktop bundle.

This module exposes a uvicorn-friendly ``app`` and a ``main`` callable used by
the packaged desktop application. The Electron shell (or PyInstaller binary)
launches this entrypoint, which serves the FastAPI backend on a configurable
host/port. CORS is enabled for the local desktop origins so the Electron
renderer (loaded from ``file://`` in production and ``http://localhost`` in
development) can reach the API.

Configuration is read from environment variables so the Electron main process
can pick a free port and pass it through:

* ``PARIS_HOST`` – interface to bind (default ``127.0.0.1``).
* ``PARIS_PORT`` – TCP port to bind (default ``8000``).

The backend stays bound to loopback by default; it is a local helper process
for the desktop UI and is not intended to be exposed on a public interface.
"""

from __future__ import annotations

import os

from fastapi.middleware.cors import CORSMiddleware

from backend.api import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# Origins used by the Electron renderer. ``null`` covers pages loaded from
# ``file://`` in the packaged app; the localhost entries cover the Vite dev
# server during development.
_LOCAL_ORIGINS = [
    "null",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


def build_app():
    """Build the desktop FastAPI app with CORS enabled for local origins."""
    app = create_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_LOCAL_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


# Importable target for ``uvicorn backend.server:app`` and PyInstaller.
app = build_app()


def _resolve_host() -> str:
    return os.environ.get("PARIS_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST


def _resolve_port() -> int:
    raw = os.environ.get("PARIS_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def main() -> None:
    """Run the backend with uvicorn using environment configuration."""
    import uvicorn

    uvicorn.run(app, host=_resolve_host(), port=_resolve_port(), log_level="info")


if __name__ == "__main__":
    main()
