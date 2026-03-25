import sys
from pathlib import Path

# ── Path fix ──────────────────────────────────────────────────────────────────
# Ensures `import lco` resolves correctly when pytest is run from inside the
# lco/ folder (the project root IS the package directory).
_project_root = Path(__file__).resolve().parent   # …/lco/
_parent = _project_root.parent                    # …/ (contains lco/ as subdir)
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

# Note: all async tests have been converted to sync using asyncio.run()
# so no pytest-asyncio configuration is needed here.