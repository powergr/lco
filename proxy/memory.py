"""
LCO-7 — Conversation Memory Compressor
========================================
For long multi-turn conversations, older turns accumulate in the messages
array and consume an increasing share of the context window — and input cost.

This module implements a rolling window strategy:
  - Keep the last N turns (configurable) uncompressed — these are the
    most relevant for the current exchange.
  - Compress older turns using the semantic compressor (LCO-5).
  - Optionally inject a ⟨memory summary⟩ block at the start of the
    messages array summarising what was discussed before the window.

This directly targets the "long chat histories" cost driver from the PRD.

Configuration
─────────────
  LCO_MEMORY_WINDOW=8       keep last 8 turns uncompressed
  LCO_MEMORY_SUMMARY=true   inject a summary block for compressed turns
  LCO_MEMORY_MODE=medium    compression mode for out-of-window turns
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .compressor import compress_text, CompressResult, CHARS_PER_TOKEN

logger = logging.getLogger("lco.memory")


@dataclass
class MemoryResult:
    turns_total: int = 0
    turns_in_window: int = 0
    turns_compressed: int = 0
    chars_original: int = 0
    chars_after: int = 0
    summary_injected: bool = False

    @property
    def tokens_saved_est(self) -> int:
        return max(0, (self.chars_original - self.chars_after) // CHARS_PER_TOKEN)

    @property
    def reduction_pct(self) -> float:
        if self.chars_original == 0:
            return 0.0
        return 100.0 * (self.chars_original - self.chars_after) / self.chars_original


def _count_turns(messages: list[dict[str, Any]]) -> int:
    """Count conversation turns (user + assistant pairs)."""
    return sum(1 for m in messages if m.get("role") in ("user", "assistant"))


def _message_text(msg: dict[str, Any]) -> str:
    """Extract plain text from a message, or empty string for non-text."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    return ""


def _is_compressible(msg: dict[str, Any]) -> bool:
    """
    True if this message can be compressed.
    System messages and tool-related messages are never touched.
    """
    role = msg.get("role", "")
    if role == "system":
        return False
    if role == "tool" or role == "function":
        return False
    if msg.get("tool_calls"):
        return False
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        return False
    return True


def _build_summary(compressed_msgs: list[dict[str, Any]]) -> str:
    """
    Build a brief summary header for the compressed turns.
    Uses first sentences from each message as a lightweight extractive summary.
    """
    lines = []
    for msg in compressed_msgs:
        role = msg.get("role", "")
        text = _message_text(msg)
        if not text:
            continue
        # Take first sentence only
        first = text.split(".")[0].strip()
        if first:
            lines.append(f"{role.capitalize()}: {first[:100]}")
    if not lines:
        return ""
    return "[ Earlier conversation ]\n" + "\n".join(lines[:8]) + "\n[ End of summary ]"


def compress_memory(
    messages: list[dict[str, Any]],
    *,
    window: int = 8,
    mode: str = "medium",
    inject_summary: bool = True,
    per_turn_token_budget: int = 150,
) -> tuple[list[dict[str, Any]], MemoryResult]:
    """
    Compress out-of-window conversation history.

    Parameters
    ──────────
    messages              : full messages array
    window                : number of most recent turns to keep uncompressed
    mode                  : compression mode for out-of-window turns
    inject_summary        : prepend a summary of compressed turns
    per_turn_token_budget : token budget per compressed turn

    Returns (compressed_messages, MemoryResult).
    If the conversation is within the window, returns messages unchanged.
    """
    result = MemoryResult(turns_total=_count_turns(messages))

    # Separate system messages (always preserved at position 0)
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    result.chars_original = sum(len(_message_text(m)) for m in messages)

    # Count non-system turns and find the window boundary
    turn_indices = [
        i for i, m in enumerate(non_system)
        if m.get("role") in ("user", "assistant")
    ]
    total_turns = len(turn_indices)
    result.turns_total = total_turns

    if total_turns <= window:
        # Within window — nothing to compress
        result.turns_in_window = total_turns
        result.chars_after = result.chars_original
        return messages, result

    # Split at window boundary
    window_start_idx = turn_indices[-window] if window > 0 else len(non_system)
    out_of_window = non_system[:window_start_idx]
    in_window = non_system[window_start_idx:]

    result.turns_in_window = window
    result.turns_compressed = len([m for m in out_of_window
                                   if m.get("role") in ("user", "assistant")])

    # Compress out-of-window turns
    compressed_old: list[dict[str, Any]] = []
    for msg in out_of_window:
        if not _is_compressible(msg):
            compressed_old.append(msg)
            continue
        text = _message_text(msg)
        if len(text) < per_turn_token_budget * CHARS_PER_TOKEN:
            compressed_old.append(msg)
            continue
        compressed_text, _ = compress_text(
            text,
            per_turn_token_budget,
            query="",  # no query for history compression
        )
        if compressed_text != text:
            compressed_old.append({**msg, "content": compressed_text})
        else:
            compressed_old.append(msg)

    # Optionally replace compressed turns with a summary block
    if inject_summary and compressed_old:
        summary_text = _build_summary(out_of_window)
        if summary_text:
            summary_msg = {
                "role": "system",
                "content": summary_text,
            }
            out_of_window_final: list[dict[str, Any]] = [summary_msg]
        else:
            out_of_window_final = compressed_old
        result.summary_injected = True
    else:
        out_of_window_final = compressed_old

    final = system_msgs + out_of_window_final + in_window
    result.chars_after = sum(len(_message_text(m)) for m in final)

    logger.info(
        "Memory: %d turns → window=%d compressed=%d  "
        "tokens_saved≈%d  reduction=%.1f%%",
        total_turns, window,
        result.turns_compressed,
        result.tokens_saved_est,
        result.reduction_pct,
    )

    return final, result