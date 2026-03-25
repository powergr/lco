"""Compatibility shim — all providers now live in lco/adapters.py."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_lco_adapters_flat",
    Path(__file__).parent.parent / "adapters.py",
)
assert _spec is not None, "Could not locate lco/adapters.py"
assert _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

BaseAdapter        = _mod.BaseAdapter
OpenAIAdapter      = _mod.OpenAIAdapter
AnthropicAdapter   = _mod.AnthropicAdapter
get_adapter        = _mod.get_adapter
_detect_provider   = _mod._detect_provider
PROVIDER_REGISTRY  = _mod.PROVIDER_REGISTRY
ANTHROPIC_MODEL_MAP = _mod.ANTHROPIC_MODEL_MAP