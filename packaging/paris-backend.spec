# -*- mode: python ; coding: utf-8 -*-
"""Backward-compatibility shim.

The canonical PyInstaller spec now lives at the repository root as
``backend.spec`` (it targets the freeze-safe ``backend/main.py`` entrypoint and
collects the full set of hidden imports). This file executes that spec so
existing invocations of ``pyinstaller packaging/paris-backend.spec`` keep
working and produce an identical ``paris-backend`` executable.
"""

import os

_ROOT = os.path.dirname(SPECPATH)  # noqa: F821 - SPECPATH injected by PyInstaller
_CANONICAL = os.path.join(_ROOT, "backend.spec")

# The canonical spec resolves the repository root from ``SPECPATH``; point it at
# the repo root so paths resolve correctly when invoked through this shim.
SPECPATH = _ROOT  # noqa: F811

with open(_CANONICAL, "r", encoding="utf-8") as _fh:
    exec(compile(_fh.read(), _CANONICAL, "exec"))
