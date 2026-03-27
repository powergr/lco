"""
LCO-6 — Output Optimizer
==========================
Reduces expensive output tokens by compressing LLM responses before
delivering them to the client.

This module provides the compress_fn that is injected into StreamBuffer.collect().
The buffer calls it after the full response is assembled (LCO-2 guarantees
the full text is available before this runs).

Modes
─────
  light      Remove trailing pleasantries, collapse excess whitespace,
             deduplicate repeated sentences. Targets ~15-25% reduction.
             Very low quality risk.

  medium     Apply sentence extraction to keep the highest-value content.
             Targets ~30-45% reduction. Low-medium quality risk.
             The quality gate (LCO-4) must approve before delivery.

  aggressive Maximum extraction. Targets ~50-65% reduction.
             Requires explicit opt-in. Quality gate threshold raised.

Safe Zones (always bypassed by the buffer before this is called):
  - Code blocks
  - Tool call deltas
  - JSON-only responses
These are guaranteed by the buffer's pre-check in LCO-2.

Quality gate integration
────────────────────────
After compression, the output is run through the quality gate.
If the score is below threshold, the original response is delivered instead.
This is the last line of defence before content reaches the user.
"""

from __future__ import annotations

import logging
import re

from .cleaner import clean_text, CleanResult
from .compressor import compress_text, CHARS_PER_TOKEN

logger = logging.getLogger("lco.output_optimizer")

# ── Token budget by mode ──────────────────────────────────────────────────────

# Fraction of original length to target as output budget
_BUDGET_FRACTION = {
    "light":      0.80,
    "medium":     0.50,
    "aggressive": 0.30, # Pushed from 0.38 to 0.30
}

# Minimum output length — never compress below this many tokens
_MIN_OUTPUT_TOKENS = 30


# ── Output-specific boilerplate ───────────────────────────────────────────────
# Patterns common in LLM outputs that add tokens with minimal information value.
# These complement the input-side cleaner patterns.

_OUTPUT_BOILERPLATE: list[re.Pattern] =[
    # New aggressive conversational strippers
    re.compile(r"^(certainly|sure|of course|absolutely|yes)[!.,]\s*", re.I | re.M),
    re.compile(r"^(here is|here are|here's) (the|some) (code|solution|information|breakdown)[^:]*:\s*", re.I | re.M),
    re.compile(r"^(based on your (request|query)|to answer your question)[^,]*,\s*", re.I | re.M),
    
    # Existing ones
    re.compile(r"^in (summary|conclusion|short)[,:]\s*", re.I | re.M),
    re.compile(r"^to summarize[,:]\s*", re.I | re.M),
    re.compile(r"^in other words[,:]\s*", re.I | re.M),
    re.compile(r"^as (i|we) (mentioned|noted|discussed) (earlier|above|before)[,.]\s*", re.I),
    re.compile(r"\bI hope (this|that) (helps?|answers? your question|clarifies)[.!]?\s*$", re.I | re.M),
    re.compile(r"\bplease (don'?t hesitate to|feel free to) (ask|reach out)[^.]*[.!]?\s*$", re.I | re.M),
    re.compile(r"\blet me know if you('?d like| need| want| have)[^.]*[.!]?\s*$", re.I | re.M),
    re.compile(r"\bis there anything else[^?]*\?\s*$", re.I | re.M),
]


def _strip_output_boilerplate(text: str) -> str:
    for pattern in _OUTPUT_BOILERPLATE:
        text = pattern.sub("", text)
    return text.strip()


# ── Main compression functions ────────────────────────────────────────────────

async def compress_output_light(text: str) -> str:
    """
    Light output compression: boilerplate removal + dedup + whitespace.
    No sentence extraction — safe for all content types.
    """
    stats = CleanResult()
    result = clean_text(text, stats)
    result = _strip_output_boilerplate(result)
    if result != text:
        reduction = len(text) - len(result)
        logger.debug("Light output: removed %d chars (~%d tokens)",
                     reduction, reduction // CHARS_PER_TOKEN)
    return result or text  # never return empty


async def compress_output_medium(text: str, query: str = "") -> str:
    """
    Medium output compression: light pass + sentence extraction.
    Targets ~40% reduction. Quality gate will approve/reject before delivery.
    """
    # First apply light pass
    stats = CleanResult()
    cleaned = clean_text(text, stats)
    cleaned = _strip_output_boilerplate(cleaned)

    # Then apply extractive compression
    token_budget = max(
        _MIN_OUTPUT_TOKENS,
        int(len(cleaned) / CHARS_PER_TOKEN * _BUDGET_FRACTION["medium"]),
    )
    compressed, comp_result = compress_text(cleaned, token_budget, query=query)

    logger.debug(
        "Medium output: %d→%d chars  sentences %d→%d  reduction=%.1f%%",
        len(text), len(compressed),
        comp_result.sentences_original, comp_result.sentences_kept,
        comp_result.char_reduction_pct,
    )
    return compressed or text


async def compress_output_aggressive(text: str, query: str = "") -> str:
    """
    Aggressive output compression: maximum extraction.
    Quality gate is the safety net — if it rejects, original is delivered.
    """
    stats = CleanResult()
    cleaned = clean_text(text, stats)
    cleaned = _strip_output_boilerplate(cleaned)

    token_budget = max(
        _MIN_OUTPUT_TOKENS,
        int(len(cleaned) / CHARS_PER_TOKEN * _BUDGET_FRACTION["aggressive"]),
    )
    compressed, comp_result = compress_text(cleaned, token_budget, query=query)

    logger.debug(
        "Aggressive output: %d→%d chars  reduction=%.1f%%",
        len(text), len(compressed), comp_result.char_reduction_pct,
    )
    return compressed or text


def make_output_compress_fn(mode: str, query: str = ""):
    """
    Factory: returns the compress_fn appropriate for the given mode.
    The returned function matches the CompressFn signature: async (str) -> str.
    This is what gets injected into StreamBuffer.collect(compress_fn=...).
    """
    if mode == "light":
        return compress_output_light

    elif mode == "medium":
        async def _medium(text: str) -> str:
            return await compress_output_medium(text, query=query)
        return _medium

    elif mode == "aggressive":
        async def _aggressive(text: str) -> str:
            return await compress_output_aggressive(text, query=query)
        return _aggressive

    else:
        # passthrough — buffer's _passthrough is already the default,
        # but returning it explicitly here keeps the router clean
        from .buffer import _passthrough
        return _passthrough