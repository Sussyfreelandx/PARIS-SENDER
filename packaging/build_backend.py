"""Build the backend executable and stage it for electron-builder.

Runs the PyInstaller spec and copies the resulting ``paris-backend`` binary
into ``electron/resources/backend/`` so electron-builder can pick it up via
``extraResources``. Cross-platform: the executable name gains a ``.exe``
suffix on Windows automatically.

Usage:

    pip install -r requirements.txt -r packaging/requirements-build.txt
    python packaging/build_backend.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Canonical spec lives at the repository root (targets backend/main.py with the
# full hidden-import set). packaging/paris-backend.spec remains as a shim.
SPEC = ROOT / "backend.spec"
DIST = ROOT / "dist"
STAGE = ROOT / "electron" / "resources" / "backend"

EXE_NAME = "paris-backend.exe" if sys.platform.startswith("win") else "paris-backend"


def run_pyinstaller() -> None:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC),
        "--clean",
        "--noconfirm",
        "--distpath",
        str(DIST),
        "--workpath",
        str(ROOT / "build"),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def stage_binary() -> None:
    source = DIST / EXE_NAME
    if not source.exists():
        raise SystemExit(f"Expected backend binary not found: {source}")
    STAGE.mkdir(parents=True, exist_ok=True)
    target = STAGE / EXE_NAME
    shutil.copy2(source, target)
    if not sys.platform.startswith("win"):
        target.chmod(0o755)
    print(f"Staged backend binary at {target}")


def main() -> None:
    run_pyinstaller()
    stage_binary()


if __name__ == "__main__":
    main()
