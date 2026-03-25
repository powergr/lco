#!/usr/bin/env python3
"""
LCO — install.py
Run this ONCE from inside the lco/ folder to register the package:

    python3 install.py

What it does:
  1. Finds your Python's site-packages directory
  2. Writes a lco.pth file that adds the PARENT of this folder to sys.path
  3. This makes `import lco` work everywhere: tests, CLI, IDE, subprocesses

Why not just `pip install -e .`?
  Editable installs with non-standard package layouts (where the project
  root IS the package) behave inconsistently across pip/setuptools/Python
  versions. A .pth file is the underlying mechanism pip uses — we just
  write it directly so it always works.
"""

import sys
import site
from pathlib import Path


def main() -> None:
    here = Path(__file__).resolve().parent     # …/lco/  (this folder)
    parent = here.parent                       # …/      (contains lco/ as a subdir)

    # Find site-packages — prefer user site if running without admin rights
    candidates = []
    try:
        candidates += site.getsitepackages()
    except AttributeError:
        pass
    if site.ENABLE_USER_SITE:
        user = Path(site.getusersitepackages())
        user.mkdir(parents=True, exist_ok=True)
        candidates.insert(0, str(user))

    if not candidates:
        print("ERROR: Could not find site-packages directory.", file=sys.stderr)
        sys.exit(1)

    # Write the .pth file to the first writable site-packages
    pth_name = "lco-dev.pth"
    written = None
    for sp in candidates:
        sp_path = Path(sp)
        pth_path = sp_path / pth_name
        try:
            sp_path.mkdir(parents=True, exist_ok=True)
            pth_path.write_text(str(parent) + "\n", encoding="utf-8")
            written = pth_path
            break
        except (PermissionError, OSError):
            continue

    if written is None:
        print(
            "ERROR: Could not write to any site-packages directory.\n"
            "Try running with admin rights, or add this path to PYTHONPATH manually:\n"
            f"  {parent}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  Written : {written}")
    print(f"  Adds    : {parent}  →  sys.path")
    print()

    # Verify it worked in this process
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))

    try:
        import lco  # noqa: F401
        import lco.proxy.safe_zones  # noqa: F401
        from lco.adapters import AnthropicAdapter  # noqa: F401
        print(f"  import lco          OK  ({lco.__file__})")
        print(f"  import lco.proxy    OK")
        print(f"  import lco.adapters OK")
        print()
        print("  Installation complete. You can now run:")
        print("    python3 cli.py start")
        print("    pytest tests/ -v")
    except ImportError as e:
        print(f"  WARNING: verification failed: {e}", file=sys.stderr)
        print("  The .pth file was written but imports failed in this process.")
        print("  Open a new terminal and try: python3 -c \"import lco\"")


if __name__ == "__main__":
    main()