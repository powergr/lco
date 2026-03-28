#!/usr/bin/env python3
"""
LCO — Build Helper
==================
Packages the tray app into a standalone executable for distribution.

  Windows:  LCO.exe   — single file, no installer needed
  macOS:    LCO.app   — drag-and-drop .app bundle
  Linux:    LCO       — standalone binary (AppImage instructions below)

Usage
─────
  pip install pyinstaller
  python3 build.py

Output lands in dist/
"""

import platform
import subprocess
import sys
from pathlib import Path

ROOT   = Path(__file__).resolve().parent
DIST   = ROOT / "dist"
BUILD  = ROOT / "build"

# Common PyInstaller flags
COMMON = [
    "pyinstaller",
    "--noconfirm",
    "--clean",
    f"--distpath={DIST}",
    f"--workpath={BUILD}",
    "--name=LCO",
    f"--paths={ROOT}",
    f"--paths={ROOT.parent}",
    # Hidden imports (same set on all platforms)
    "--hidden-import=pystray._win32",
    "--hidden-import=pystray._darwin",
    "--hidden-import=pystray._gtk",
    "--hidden-import=pystray._xorg",
    "--hidden-import=PIL",
    "--hidden-import=PIL.Image",
    "--hidden-import=PIL.ImageDraw",
    "--hidden-import=uvicorn",
    "--hidden-import=uvicorn.loops.auto",
    "--hidden-import=uvicorn.protocols.http.auto",
    "--hidden-import=uvicorn.protocols.websockets.auto",
    "--hidden-import=uvicorn.lifespan.on",
    "--hidden-import=fastapi",
    "--hidden-import=fastapi.middleware.cors",
    "--hidden-import=lco",
    "--hidden-import=lco.main",
    "--hidden-import=lco.config",
    "--hidden-import=lco.adapters",
    "--hidden-import=lco.proxy.router",
    "--hidden-import=lco.proxy.buffer",
    "--hidden-import=lco.proxy.cleaner",
    "--hidden-import=lco.proxy.compressor",
    "--hidden-import=lco.proxy.memory",
    "--hidden-import=lco.proxy.quality_gate",
    "--hidden-import=lco.proxy.output_optimizer",
    "--hidden-import=lco.proxy.llm_compressor",
    "--hidden-import=lco.proxy.safe_zones",
    "--hidden-import=lco.proxy.dashboard",
    "--hidden-import=lco.storage.metrics",
    "--hidden-import=lco.middleware.metrics",
    "--hidden-import=anyio",
    "--hidden-import=anyio._backends._asyncio",
    "--hidden-import=aiosqlite",
    "--hidden-import=httpx",
    "--hidden-import=tkinter",
    "--hidden-import=tkinter.font",
    # Include lco package directory
    f"--add-data={ROOT}{':' if sys.platform != 'win32' else ';'}lco",
    # Exclude heavy packages not needed
    "--exclude-module=matplotlib",
    "--exclude-module=numpy",
    "--exclude-module=pandas",
    "--exclude-module=PyQt5",
    "--exclude-module=wx",
    "--noupx",           # disable UPX — triggers AV false positives
    str(ROOT / "tray.py"),
]


def build_windows() -> None:
    """Single-file .exe using the spec file (no UPX, no console window)."""
    print("Building LCO.exe for Windows (via build_windows.spec)...")
    spec = ROOT / "build_windows.spec"
    if not spec.exists():
        print(f"ERROR: {spec} not found")
        sys.exit(1)
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        f"--distpath={DIST}",
        f"--workpath={BUILD}",
        str(spec),
    ]
    subprocess.run(cmd, check=True)
    print(f"\n✓ Output: {DIST / 'LCO.exe'}")
    print("  UPX disabled — safe for Windows Defender and other AV.")
    print("  Distribute the single LCO.exe file. No installer needed.")


def build_macos() -> None:
    """macOS .app bundle — drag to Applications folder."""
    print("Building LCO.app for macOS...")
    cmd = COMMON + ["--windowed", "--onedir"]
    # Uncomment to add a custom icon:
    # cmd += ["--icon=assets/lco.icns"]
    subprocess.run(cmd, check=True)
    app = DIST / "LCO.app"
    print(f"\n✓ Output: {app}")
    print("  Drag LCO.app to /Applications and double-click to launch.")
    print("  To sign for distribution:")
    print("    codesign --deep --force --sign 'Developer ID Application: YOU' dist/LCO.app")
    print("    xcrun notarytool submit dist/LCO.zip --apple-id you@example.com \\")
    print("          --team-id YOURTEAMID --password @keychain:AC_PASSWORD")


def build_linux() -> None:
    """Linux standalone binary. Optionally wrap in AppImage."""
    print("Building LCO for Linux...")
    cmd = COMMON + ["--onefile"]
    subprocess.run(cmd, check=True)
    binary = DIST / "LCO"
    print(f"\n✓ Output: {binary}")
    print("  Run directly:  ./dist/LCO")
    print()
    print("  To wrap in an AppImage (optional, for wider distribution):")
    print("    wget https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage")
    print("    chmod +x appimagetool-x86_64.AppImage")
    print("    # Create AppDir structure:")
    print("    mkdir -p AppDir/usr/bin")
    print("    cp dist/LCO AppDir/usr/bin/")
    print("    # Add AppDir/lco.desktop and AppDir/lco.png, then:")
    print("    ./appimagetool-x86_64.AppImage AppDir LCO.AppImage")


if __name__ == "__main__":
    os_name = platform.system()
    print(f"Platform: {os_name}\n")

    # Check PyInstaller
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not installed. Run:  pip install pyinstaller")
        sys.exit(1)

    if os_name == "Windows":
        build_windows()
    elif os_name == "Darwin":
        build_macos()
    elif os_name == "Linux":
        build_linux()
    else:
        print(f"Unknown platform: {os_name}")
        print("Run the COMMON PyInstaller flags manually from build.py")
        sys.exit(1)