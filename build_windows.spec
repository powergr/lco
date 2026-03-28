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
# UPX is intentionally disabled — UPX-packed executables are routinely
# flagged as malware by Windows Defender and other AV products.

from pathlib import Path

ROOT = Path(SPECPATH)

block_cipher = None

a = Analysis(
    [str(ROOT / 'tray.py')],
    pathex=[str(ROOT), str(ROOT.parent)],
    binaries=[],
    datas=[
        (str(ROOT), 'lco'),
    ],
    hiddenimports=[
        'pystray._win32',
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
        'uvicorn', 'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'fastapi', 'fastapi.middleware', 'fastapi.middleware.cors',
        'lco', 'lco.main', 'lco.config', 'lco.version',
        'lco.adapters',
        'lco.proxy.router', 'lco.proxy.buffer', 'lco.proxy.cleaner',
        'lco.proxy.compressor', 'lco.proxy.memory',
        'lco.proxy.quality_gate', 'lco.proxy.output_optimizer',
        'lco.proxy.llm_compressor', 'lco.proxy.safe_zones',
        'lco.proxy.dashboard',
        'lco.storage.metrics', 'lco.middleware.metrics',
        'anyio', 'anyio._backends._asyncio',
        'aiosqlite', 'httpx', 'dotenv', 'typer',
        'multiprocessing', 'tkinter', 'tkinter.font',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'PyQt5', 'PyQt6', 'wx', 'gi',
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
    upx=False,           # DISABLED — UPX triggers AV false positives
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # no console window — pure tray app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Uncomment to embed a custom icon:
    # icon=str(ROOT / 'assets' / 'lco.ico'),
)