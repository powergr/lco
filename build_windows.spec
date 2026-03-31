# -*- mode: python ; coding: utf-8 -*-
#
# LCO — Windows build spec
#
# Usage:
#   pyinstaller build_windows.spec
#
# Output:
#   dist\LCO.exe  (single file, no console, NO UPX)
#
# Three critical fixes in this version:
#
# 1. SSL certificates (certifi/cacert.pem) are explicitly bundled.
#    Without this, ALL HTTPS providers (OpenRouter, Groq, Mistral, etc.)
#    fail with SSLError → HTTP 500 "Internal proxy error".
#    The runtime hook (hooks/rthook_ssl.py) sets SSL_CERT_FILE at startup.
#
# 2. trio is excluded. It conflicts with PyInstaller's asyncio freeze,
#    causing: KeyError: 'asyncio' in trio._subprocess_platform.
#    httpx/anyio work fine on the asyncio backend without trio.
#
# 3. httpcore backends are explicitly imported so httpx works in frozen mode.

import sys
from pathlib import Path
import certifi

ROOT = Path(SPECPATH)

block_cipher = None

# Path to the certifi CA bundle on the build machine
CERTIFI_PEM = certifi.where()

a = Analysis(
    [str(ROOT / 'tray.py')],
    pathex=[str(ROOT), str(ROOT.parent)],
    binaries=[],
    datas=[
        # LCO package
        (str(ROOT), 'lco'),
        # SSL CA certificates — required for HTTPS to OpenRouter, Groq, etc.
        (CERTIFI_PEM, 'certifi'),
    ],
    hiddenimports=[
        # pystray Windows backend
        'pystray._win32',
        # PIL
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
        # FastAPI / uvicorn
        'uvicorn', 'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'fastapi', 'fastapi.middleware', 'fastapi.middleware.cors',
        # LCO modules
        'lco', 'lco.main', 'lco.config', 'lco.version',
        'lco.adapters',
        'lco.proxy.router', 'lco.proxy.buffer', 'lco.proxy.cleaner',
        'lco.proxy.compressor', 'lco.proxy.memory',
        'lco.proxy.quality_gate', 'lco.proxy.output_optimizer',
        'lco.proxy.llm_compressor', 'lco.proxy.safe_zones',
        'lco.proxy.dashboard',
        'lco.storage.metrics', 'lco.middleware.metrics',
        # httpx / httpcore — asyncio backend only (trio excluded)
        'httpx', 'httpcore',
        'httpcore._async', 'httpcore._async.connection',
        'httpcore._async.connection_pool',
        'httpcore._async.http11',
        'httpcore._sync', 'httpcore._sync.connection',
        'httpcore._sync.connection_pool',
        'httpcore._sync.http11',
        # anyio asyncio backend (trio excluded)
        'anyio', 'anyio._backends._asyncio',
        # Certifi — needed for SSL cert lookup
        'certifi',
        # Async / storage
        'aiosqlite', 'dotenv', 'typer',
        'multiprocessing', 'tkinter', 'tkinter.font',
    ],
    hookspath=['hooks'],
    runtime_hooks=['hooks/rthook_ssl.py'],
    excludes=[
        # trio conflicts with PyInstaller's asyncio freeze
        # → KeyError: 'asyncio' in trio._subprocess_platform
        'trio', 'trio._subprocess_platform',
        # Other unused heavy packages
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'PyQt5', 'PyQt6', 'wx', 'gi',
        # pystray backends not needed on Windows
        'pystray._darwin', 'pystray._gtk', 'pystray._xorg',
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
    name='LCO',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Uncomment after creating assets\lco.ico:
    # icon=str(ROOT / 'assets' / 'lco.ico'),
)