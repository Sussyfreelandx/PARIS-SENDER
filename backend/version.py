"""Single source of truth for the backend application version.

Kept in one place so the value can be surfaced through the API (``/version`` and
``/health``) and used by the diagnostics panel to detect a frontend/backend
mismatch. Bump this in lockstep with ``electron/package.json`` on release.
"""

from __future__ import annotations

__all__ = ["__version__", "BACKEND_VERSION"]

__version__ = "0.2.0"
BACKEND_VERSION = __version__
