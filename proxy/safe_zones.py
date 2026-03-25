"""
LCO — Safe Zones
Detects message content that must never be modified by any compression pass.

Hard exclusions (always bypassed):
  • Tool / function call inputs and outputs
  • Code blocks (fenced with ``` or indented)
  • Structured JSON payloads
  • System messages tagged with lco-safe

This module is intentionally pure-Python with no external deps so it
can be used early in the request pipeline before anything async happens.
"""

from __future__ import annotations
import json
import re
from enum import Enum
from typing import Any

# ── Fenced code block pattern ────────────────────────────────────────────────
_CODE_FENCE_RE = re.compile(r"```", re.MULTILINE)
_INDENTED_CODE_RE = re.compile(r"^( {4}|\t).+", re.MULTILINE)


class SafeZoneReason(str, Enum):
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CODE_BLOCK = "code_block"
    JSON_PAYLOAD = "json_payload"
    SAFE_TAG = "lco_safe_tag"
    NONE = "none"


def is_tool_call_message(message: dict[str, Any]) -> tuple[bool, SafeZoneReason]:
    """
    True for any message that carries tool/function call data.
    These are programmatic contracts — never compress.
    """
    role = message.get("role", "")
    content = message.get("content")

    # OpenAI: assistant message with tool_calls array
    if role == "assistant" and message.get("tool_calls"):
        return True, SafeZoneReason.TOOL_CALL

    # OpenAI: tool result message
    if role == "tool":
        return True, SafeZoneReason.TOOL_RESULT

    # Legacy OpenAI function calling
    if role == "function":
        return True, SafeZoneReason.TOOL_RESULT

    # Anthropic: assistant message with tool_use content block
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in (
                "tool_use",
                "tool_result",
            ):
                return True, SafeZoneReason.TOOL_CALL

    return False, SafeZoneReason.NONE


def has_code_block(text: str) -> bool:
    """True if the text contains a fenced or indented code block."""
    fences = _CODE_FENCE_RE.findall(text)
    # Fenced blocks come in pairs (opening + closing)
    if len(fences) >= 2:
        return True
    if _INDENTED_CODE_RE.search(text):
        return True
    return False


def is_json_payload(text: str) -> bool:
    """
    True if the entire text (stripped) is valid JSON.
    Partial JSON inside prose doesn't count.
    """
    stripped = text.strip()
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return False
    try:
        json.loads(stripped)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def has_safe_tag(text: str) -> bool:
    """Honour explicit lco-safe annotation in system messages."""
    return "<!-- lco-safe -->" in text or "<lco-safe/>" in text


def check_message(message: dict[str, Any]) -> tuple[bool, SafeZoneReason]:
    """
    Single entry-point: returns (is_safe_zone, reason).
    If is_safe_zone is True, the message must be passed through unmodified.
    """
    # 1. Tool calls take priority — check before touching content
    is_tool, reason = is_tool_call_message(message)
    if is_tool:
        return True, reason

    content = message.get("content", "")

    # 2. Content may be a list of blocks (Anthropic format)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type in ("tool_use", "tool_result", "image"):
                    return True, SafeZoneReason.TOOL_CALL
                text = block.get("text", "")
                if isinstance(text, str):
                    if has_safe_tag(text):
                        return True, SafeZoneReason.SAFE_TAG
                    if has_code_block(text):
                        return True, SafeZoneReason.CODE_BLOCK
                    if is_json_payload(text):
                        return True, SafeZoneReason.JSON_PAYLOAD
        return False, SafeZoneReason.NONE

    # 3. Plain string content
    if not isinstance(content, str):
        # Unknown format — be conservative
        return True, SafeZoneReason.SAFE_TAG

    if has_safe_tag(content):
        return True, SafeZoneReason.SAFE_TAG
    if has_code_block(content):
        return True, SafeZoneReason.CODE_BLOCK
    if is_json_payload(content):
        return True, SafeZoneReason.JSON_PAYLOAD

    return False, SafeZoneReason.NONE


def classify_messages(
    messages: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], bool, SafeZoneReason]]:
    """
    Returns a list of (message, is_safe_zone, reason) tuples for the
    entire messages array. Used by the compression pipeline to decide
    which messages it is allowed to touch.
    """
    return [(msg, *check_message(msg)) for msg in messages]
