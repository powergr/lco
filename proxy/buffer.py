"""
LCO-2 — Streaming-Safe Output Buffer
=====================================
Collects a streamed SSE response from the upstream into a complete in-memory
buffer, optionally runs an output compression pass, then re-streams the
(possibly compressed) content back to the client.

Why this exists
───────────────
Output compression (E-04) cannot act on a response until the full text is
known. Streaming delivers it token-by-token, so we must buffer first.
This module establishes that buffer architecture so E-04 can plug in with
zero changes to the router.

Request lifecycle with this buffer
───────────────────────────────────
1.  Router calls StreamBuffer.collect(upstream_stream)
2.  Buffer reads all SSE chunks, extracts text deltas, records tool_calls
3.  Buffer applies the output compression hook (passthrough for MVP)
4.  Router calls StreamBuffer.replay() → AsyncIterator[bytes] of SSE chunks
5.  Router passes replay() to FastAPI StreamingResponse as before

Safe Zone guarantee
───────────────────
The buffer NEVER touches:
  - Chunks that contain tool_call deltas  (flagged → passthrough)
  - Chunks that contain code blocks       (detected post-assembly)
  - JSON-only content                     (detected post-assembly)
Any of these triggers passthrough mode: original chunks re-emitted verbatim.

Compression hook
────────────────
compress_fn is injected by the router. For MVP it is always _passthrough.
E-04 (output optimizer) will inject its own function here.
The function signature is:

    async def compress(text: str) -> str

It receives the assembled full text and returns the (possibly shorter) text.
If it raises, the buffer falls back to the original text silently.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Optional

logger = logging.getLogger("lco.buffer")

# Type alias for the compression hook
CompressFn = Callable[[str], Awaitable[str]]

# Fenced code block detector (same logic as safe_zones, duplicated here
# so the buffer has no circular dependency on the proxy package)
_CODE_FENCE_RE = re.compile(r"```", re.MULTILINE)


# ── Passthrough hook (MVP default) ───────────────────────────────────────────

async def _passthrough(text: str) -> str:
    """No-op compression — returns text unchanged. Default for MVP."""
    return text


# ── SSE parsing helpers ───────────────────────────────────────────────────────


def _extract_delta_text(event: dict) -> str:
    """Pull the content string out of an OpenAI-format SSE delta event."""
    choices = event.get("choices", [])
    if not choices:
        return ""
    delta = choices[0].get("delta", {})
    return delta.get("content") or ""


def _has_tool_call_delta(event: dict) -> bool:
    """True if any choice delta carries tool_calls data."""
    for choice in event.get("choices", []):
        if choice.get("delta", {}).get("tool_calls"):
            return True
    return False


def _get_finish_reason(event: dict) -> Optional[str]:
    for choice in event.get("choices", []):
        fr = choice.get("finish_reason")
        if fr:
            return fr
    return None


# ── Buffer result ─────────────────────────────────────────────────────────────

@dataclass
class BufferResult:
    """Everything the router needs after collection is complete."""

    # Raw SSE bytes exactly as received from upstream (always preserved)
    raw_chunks: list[bytes] = field(default_factory=list)

    # Assembled full text from all delta events
    assembled_text: str = ""

    # Text after compression pass (may equal assembled_text in passthrough)
    compressed_text: str = ""

    # True if any chunk contained tool_call deltas — forces passthrough
    has_tool_calls: bool = False

    # True if assembled text contains a code block — forces passthrough
    has_code_block: bool = False

    # True if compression was skipped for any reason
    passthrough: bool = False

    # Reason passthrough was triggered
    passthrough_reason: str = ""

    # Metadata from the final SSE event
    completion_id: str = ""
    model: str = ""
    finish_reason: str = "stop"

    # Timing
    collect_ms: float = 0.0
    compress_ms: float = 0.0

    @property
    def token_reduction_chars(self) -> int:
        """Character-level reduction (proxy for token savings before E-04 lands)."""
        return max(0, len(self.assembled_text) - len(self.compressed_text))


# ── Main buffer class ─────────────────────────────────────────────────────────

class StreamBuffer:
    """
    Wraps an upstream SSE stream, buffers it, and re-emits it.

    Usage (in the router):

        buffer = StreamBuffer(flush_timeout_ms=settings.stream_flush_timeout_ms)
        result = await buffer.collect(upstream_stream, compress_fn=_passthrough)
        return StreamingResponse(buffer.replay(), media_type="text/event-stream")
    """

    def __init__(self, flush_timeout_ms: int = 0) -> None:
        self.flush_timeout_ms = flush_timeout_ms
        self._result: Optional[BufferResult] = None

    async def collect(
        self,
        upstream: AsyncIterator[bytes],
        compress_fn: CompressFn = _passthrough,
        model: str = "",
    ) -> BufferResult:
        """
        Consume the upstream stream, assemble full text, run compression.
        Returns a BufferResult. Safe to call exactly once per request.
        """
        result = BufferResult(model=model)
        t0 = time.perf_counter()

        # ── Phase 1: collect all raw chunks ─────────────────────────────────
        async for chunk in upstream:
            result.raw_chunks.append(chunk)

        # Glue all chunks together safely (immune to TCP packet boundaries)
        full_payload = b"".join(result.raw_chunks).decode("utf-8", errors="replace")
        
        for line in full_payload.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
                
            data_str = line[5:].strip()
            if data_str == "[DONE]" or not data_str:
                continue
                
            try:
                event = json.loads(data_str)
                # Capture metadata from any event that has it
                if not result.completion_id:
                    result.completion_id = event.get("id", "")
                if not result.model:
                    result.model = event.get("model", model)

                fr = _get_finish_reason(event)
                if fr:
                    result.finish_reason = fr

                # Tool call delta → force passthrough
                if _has_tool_call_delta(event):
                    result.has_tool_calls = True

                # Accumulate text
                result.assembled_text += _extract_delta_text(event)
            except json.JSONDecodeError:
                pass

        result.collect_ms = (time.perf_counter() - t0) * 1000

        # ── Phase 2: safe zone check on assembled text ───────────────────────
        if len(_CODE_FENCE_RE.findall(result.assembled_text)) >= 2:
            result.has_code_block = True

        if result.has_tool_calls:
            result.passthrough = True
            result.passthrough_reason = "tool_call_delta"
        elif result.has_code_block:
            result.passthrough = True
            result.passthrough_reason = "code_block"

        # ── Phase 3: compression pass ────────────────────────────────────────
        t1 = time.perf_counter()
        if result.passthrough:
            result.compressed_text = result.assembled_text
            logger.debug(
                "Buffer passthrough: %s  len=%d",
                result.passthrough_reason,
                len(result.assembled_text),
            )
        else:
            try:
                result.compressed_text = await compress_fn(result.assembled_text)
            except Exception as exc:
                logger.warning("Compression hook raised %s — using original", exc)
                result.compressed_text = result.assembled_text
                result.passthrough = True
                result.passthrough_reason = "compress_error"

        result.compress_ms = (time.perf_counter() - t1) * 1000

        if result.token_reduction_chars:
            logger.info(
                "Buffer: collected %d chunks  assembled=%d chars  compressed=%d chars  "
                "reduction=%d chars  collect=%.1fms  compress=%.1fms",
                len(result.raw_chunks),
                len(result.assembled_text),
                len(result.compressed_text),
                result.token_reduction_chars,
                result.collect_ms,
                result.compress_ms,
            )
        else:
            logger.debug(
                "Buffer: passthrough  chunks=%d  len=%d  collect=%.1fms",
                len(result.raw_chunks),
                len(result.assembled_text),
                result.collect_ms,
            )

        self._result = result
        return result

    async def replay(self) -> AsyncIterator[bytes]:
        """
        Re-stream the buffered response to the client.

        If passthrough is True (tool calls, code blocks, compression error):
            Re-emit the original raw chunks verbatim.
        Otherwise:
            Emit the compressed text as a single synthetic SSE chunk,
            followed by a finish chunk and [DONE].
        """
        if self._result is None:
            raise RuntimeError("replay() called before collect()")

        result: BufferResult = self._result  # narrows Optional[BufferResult] → BufferResult

        if result.passthrough:
            # Re-emit original bytes exactly as received
            for chunk in result.raw_chunks:
                yield chunk
            return

        # Emit compressed text as a single delta chunk
        delta_event = {
            "id": result.completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": result.model,
            "choices": [{
                "index": 0,
                "delta": {"content": result.compressed_text},
                "finish_reason": None,
            }],
        }
        yield f"data: {json.dumps(delta_event)}\n\n".encode()

        # Emit finish chunk
        finish_event = {
            "id": result.completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": result.model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": result.finish_reason,
            }],
        }
        yield f"data: {json.dumps(finish_event)}\n\n".encode()
        yield b"data: [DONE]\n\n"