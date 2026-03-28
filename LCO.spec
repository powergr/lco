# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\power\\Documents\\lco\\tray.py'],
    pathex=['C:\\Users\\power\\Documents\\lco', 'C:\\Users\\power\\Documents'],
    binaries=[],
    datas=[('C:\\Users\\power\\Documents\\lco', 'lco')],
    hiddenimports=['pystray._win32', 'pystray._darwin', 'pystray._gtk', 'pystray._xorg', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'uvicorn', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'fastapi', 'fastapi.middleware.cors', 'lco', 'lco.main', 'lco.config', 'lco.adapters', 'lco.proxy.router', 'lco.proxy.buffer', 'lco.proxy.cleaner', 'lco.proxy.compressor', 'lco.proxy.memory', 'lco.proxy.quality_gate', 'lco.proxy.output_optimizer', 'lco.proxy.llm_compressor', 'lco.proxy.safe_zones', 'lco.proxy.dashboard', 'lco.storage.metrics', 'lco.middleware.metrics', 'anyio', 'anyio._backends._asyncio', 'aiosqlite', 'httpx', 'tkinter', 'tkinter.font'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'PyQt5', 'wx'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LCO',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
