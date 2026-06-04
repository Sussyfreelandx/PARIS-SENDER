"""Frozen entrypoint for the PyInstaller backend bundle.

Thin wrapper around :func:`backend.main.run`, which adds the project root to
``sys.path`` (freeze-safe) and writes any startup failure to ``logs/startup.log``
so the packaged executable never exits silently. All server logic lives in
``backend/server.py``; the robust launch/crash-logging logic lives in
``backend/main.py``.
"""

from __future__ import annotations

import multiprocessing
import os
import sys

# Ensure the repository root (parent of this ``packaging`` directory) is on the
# import path. When this file is executed directly as a script, Python puts the
# ``packaging`` directory first on ``sys.path`` instead of the repo root, which
# would make ``import backend`` fail. PyInstaller bundles the package, but this
# keeps the source/script path working identically.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.main import run

if __name__ == "__main__":
    # Required so frozen executables do not re-spawn the app when libraries use
    # multiprocessing internally.
    multiprocessing.freeze_support()
    run()
