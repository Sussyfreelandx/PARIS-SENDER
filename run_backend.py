"""Standalone backend entrypoint for running / packaging the FastAPI server.

Running the FastAPI app via this root-level script (instead of executing
``backend/api/app.py`` directly) guarantees the ``backend`` package is on the
import path and imported as a proper package, so absolute imports such as
``from backend.services.delivery import DeliveryService`` resolve correctly —
both when run with ``python run_backend.py`` and when frozen by PyInstaller.

Usage:

    python run_backend.py

Note: the packaged desktop app uses ``packaging/backend_entry.py`` ->
``backend.server`` instead, because that path is environment-aware
(``PARIS_HOST`` / ``PARIS_PORT``, CORS) and freeze-safe; the Electron shell
relies on it to bind a dynamically chosen port. This script is the simple
fixed-port (127.0.0.1:8000) entrypoint described in the build instructions.
"""

from backend.api.app import app
import uvicorn


def main():
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
