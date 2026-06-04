# -*- mode: python ; coding: utf-8 -*-
"""Canonical PyInstaller spec for the Paris Sender FastAPI backend.

Produces a single self-contained executable named ``paris-backend`` (with the
platform-appropriate ``.exe`` suffix on Windows) from the freeze-safe entrypoint
``backend/main.py``. The Electron desktop shell launches this binary as a child
process and talks to it over loopback HTTP, waiting for ``/health`` before
opening the UI.

Why this spec lives at the repository root: PyInstaller resolves the script path
and ``pathex`` relative to the spec file's directory, so keeping it at the root
makes ``backend/main.py`` and the ``backend`` package resolve without ``..``
gymnastics. ``packaging/paris-backend.spec`` is a thin shim that execs this file
for backward compatibility.

Build:

    pip install -r requirements.txt -r packaging/requirements-build.txt
    pyinstaller backend.spec --clean --noconfirm

The output binary is written to ``dist/paris-backend`` and is copied into the
Electron app as an extra resource by ``packaging/build_backend.py``.
"""

import os

from PyInstaller.utils.hooks import collect_submodules

# Directory containing this spec (the repository root). ``SPECPATH`` is injected
# by PyInstaller when the spec is executed.
ROOT = SPECPATH  # noqa: F821 - provided by PyInstaller at exec time

# Entrypoint: the robust launcher with crash logging.
ENTRYPOINT = os.path.join(ROOT, "backend", "main.py")

# Packages whose submodules PyInstaller's static analysis can miss because they
# are imported lazily/dynamically at runtime (uvicorn loads its protocol/loop
# implementations on demand; cryptography/keyring/dns/bcrypt are pulled in only
# when the relevant feature runs).
_COLLECT_PACKAGES = (
    "backend",
    "uvicorn",
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_core",
    "anyio",
    "jinja2",
    "cryptography",
    "email",
    "dns",        # dnspython
    "keyring",
)

hidden_imports = []
for package in _COLLECT_PACKAGES:
    try:
        hidden_imports += collect_submodules(package)
    except Exception:
        # A missing optional package must not break the build; the feature that
        # needs it simply will not be importable until it is installed.
        pass

# Explicit leaf modules that may be loaded by name and are easy for static
# analysis to miss entirely.
hidden_imports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "sqlite3",
    "bcrypt",
    "dkim",
    "aiosmtplib",
]

# De-duplicate while preserving order.
hidden_imports = list(dict.fromkeys(hidden_imports))

block_cipher = None

a = Analysis(
    [ENTRYPOINT],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy optional/legacy dependencies that are not needed by the API
        # server process keep the bundle small.
        "tkinter",
        "matplotlib",
        "selenium",
        "undetected_chromedriver",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="paris-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
