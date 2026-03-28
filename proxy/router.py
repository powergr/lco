"""
LCO — Proxy Router  (Phase 2 complete)
Full request pipeline:
  1. Parse body + detect provider
  2. Safe Zone classification
  3. Memory compression (LCO-7)  — when LCO_MEMORY_COMPRESSION=true
  4. Input cleaner (LCO-3)       — when mode != passthrough
  5. Semantic compressor (LCO-5) — when mode = medium | aggressive
  6. Input quality gate (LCO-4)
  7. Forward to upstream
  8. Streaming: buffer → output optimizer (LCO-6) → quality gate → replay
     Blocking: return JSON response
  9. Record metrics
"""

from __future__ import annotations
import logging
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..adapters import get_adapter
from ..config import settings
from ..proxy.safe_zones import classify_messages
from ..proxy.buffer import StreamBuffer, _passthrough
from ..proxy.cleaner import clean_messages
from ..proxy.compressor import compress_messages
from ..proxy.output_optimizer import make_output_compress_fn
from ..proxy.memory import compress_memory
from ..proxy.quality_gate import get_quality_gate
from ..proxy.quality_gate import reset_quality_gate as _reset_gate
from ..proxy.llm_compressor import get_llm_compressor
from ..storage.metrics import MetricsDB, RequestRecord
from ..version import __version__ as _VERSION

logger = logging.getLogger("lco.proxy")


def _has_code_or_json(text: str) -> bool:
    """True only if the ENTIRE response is code or JSON (skip compression)."""
    import json as _json
    s = text.strip()
    # Pure JSON object/array
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            _json.loads(s); return True
        except Exception:
            pass
    # Pure code block (nothing outside the fences)
    import re as _re
    without_code = _re.sub(r"```[\s\S]*?```", "", s).strip()
    if not without_code:
        return True  # entire response is code blocks
    return False


async def _compress_mixed_content(text: str, compress_fn) -> str:
    """
    Async: compress prose sections of a mixed prose+code response.
    Code blocks (``` fences) are preserved exactly.
    Must be awaited — uses async compress_fn directly (no run_until_complete).
    """
    import re as _re

    pattern = _re.compile(r"(```[\s\S]*?```)", _re.MULTILINE)
    parts = pattern.split(text)

    if len(parts) == 1:
        # No code blocks — compress the whole thing
        return await compress_fn(text)

    compressed_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Code block — preserve exactly
            compressed_parts.append(part)
        else:
            # Prose section — compress if non-trivial
            stripped = part.strip()
            if len(stripped) > 80:
                try:
                    cp = await compress_fn(stripped)
                    # Preserve surrounding whitespace structure
                    prefix = "\n\n" if part.startswith("\n") else ""
                    suffix = "\n\n" if part.endswith("\n") else ""
                    compressed_parts.append(prefix + cp + suffix)
                except Exception:
                    compressed_parts.append(part)
            else:
                compressed_parts.append(part)

    return "".join(compressed_parts).strip()

router = APIRouter()
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.upstream_timeout),
            follow_redirects=True,
        )
    return _client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lower_headers(request: Request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items()}


def _count_safe_zone_hits(classifications: list) -> int:
    return sum(1 for _, is_safe, _ in classifications if is_safe)


def _extract_usage(body: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = body.get("usage", {})
    inp = usage.get("prompt_tokens") or usage.get("input_tokens")
    out = usage.get("completion_tokens") or usage.get("output_tokens")
    total = usage.get("total_tokens")
    if inp is not None and out is not None and total is None:
        total = inp + out
    return inp, out, total


def _last_user_query(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                return content[:500]
    return ""


async def _streaming_response(
    stream: AsyncIterator[bytes],
    record: RequestRecord,
    provider_name: str,
    safe_hits: int,
    model: str,
    query: str,
) -> AsyncIterator[bytes]:
    mode = settings.compression_mode

    if mode != "passthrough" and settings.output_optimization:
        compress_fn = make_output_compress_fn(mode, query=query)

        # After compression, run the quality gate
        # Handles both plain prose and mixed prose+code responses
        async def gated_compress(text: str) -> str:
            import re as _re2
            has_code_blocks = bool(_re2.search(r"```", text))
            if _has_code_or_json(text):
                return text  # pure code/JSON — skip
            if has_code_blocks:
                compressed = await _compress_mixed_content(text, compress_fn)
            else:
                compressed = await compress_fn(text)
            if compressed == text:
                return text
            gate = get_quality_gate()
            gate_result = await gate.check(text, compressed)
            record.quality_score = gate_result.score
            if not gate_result.passed:
                logger.warning(
                    "Output quality gate rejected compression (score=%.3f) — reverting",
                    gate_result.score,
                )
                return text
            return compressed
    else:
        gated_compress = _passthrough

    buf = StreamBuffer(flush_timeout_ms=settings.stream_flush_timeout_ms)
    result = await buf.collect(stream, compress_fn=gated_compress, model=model)
    record.streaming = True
    record.compression_mode = mode

    # Estimate output tokens saved from character reduction
    if result.token_reduction_chars > 0:
        record.output_tokens_saved_est = result.token_reduction_chars // 4

    # Yield a synthetic header chunk so caller can read savings
    # (actual HTTP headers already sent; we communicate via x-lco-output-saved
    #  which is set on the StreamingResponse headers above)
    async for chunk in buf.replay():
        yield chunk


# ── Main proxy route ──────────────────────────────────────────────────────────

@router.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def proxy(request: Request, path: str) -> Any:
    headers = _lower_headers(request)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    adapter = get_adapter(
        headers, body, get_client(),
        openai_base_url=settings.openai_base_url,
        anthropic_base_url=settings.anthropic_base_url,
    )
    provider_name = type(adapter).__name__.replace("Adapter", "").lower()

    record: RequestRecord = getattr(request.state, "metrics", RequestRecord(
        provider=provider_name,
        model=body.get("model", "unknown"),
        path=f"/v1/{path}",
    ))
    record.provider = provider_name
    record.model = body.get("model", "unknown")
    record.path = f"/v1/{path}"
    record.compression_mode = settings.compression_mode

    messages: list[dict[str, Any]] = body.get("messages", [])
    classifications = classify_messages(messages)
    safe_hits = _count_safe_zone_hits(classifications)
    record.safe_zone_hit = safe_hits > 0

    if safe_hits:
        logger.debug("Safe zone: %d/%d messages protected", safe_hits, len(messages))

    # Allow per-request mode override via header (used by benchmark + testing)
    # Falls back to global settings if not provided or invalid
    _valid_modes = {"passthrough", "light", "medium", "aggressive"}
    _override = headers.get("x-lco-mode-override", "").lower().strip()
    mode = _override if _override in _valid_modes else settings.compression_mode
    optimised_body = body
    query = _last_user_query(messages)

    if mode != "passthrough" and messages:

        # ── LCO-7: Memory compression ─────────────────────────────────────
        if settings.memory_compression:
            mem_msgs, mem_result = compress_memory(
                messages,
                window=settings.memory_window,
                mode=mode,
                inject_summary=settings.memory_inject_summary,
            )
            if mem_result.tokens_saved_est:
                messages = mem_msgs
                optimised_body = {**body, "messages": messages}
                logger.debug("Memory: saved ~%d tokens", mem_result.tokens_saved_est)

        # ── LCO-3: Input cleaner ──────────────────────────────────────────
        cleaned_msgs, clean_stats = clean_messages(messages, skip_last_user=True)
        if clean_stats.messages_modified:
            messages = cleaned_msgs
            optimised_body = {**optimised_body, "messages": messages}
            record.input_tokens_saved_est = clean_stats.char_reduction // 4
            logger.debug("Cleaner: -%d chars (~%d tokens)",
                         clean_stats.char_reduction,
                         clean_stats.char_reduction // 4)

        # ── LCO-5: Semantic compressor (medium / aggressive only) ─────────
        if mode in ("medium", "aggressive"):
            sem_msgs, sem_results = compress_messages(
                messages,
                mode=mode,
                skip_last_user=True,
            )
            total_saved = sum(r.estimated_tokens_saved for r in sem_results)
            if total_saved > 0:
                messages = sem_msgs
                optimised_body = {**optimised_body, "messages": messages}
                record.input_tokens_saved_est += total_saved
                logger.debug("Semantic: -%d tokens", total_saved)

        # ── LLM-assisted compression (medium / aggressive, Ollama available) ─
        # Runs after extractive compression. Only processes messages still
        # longer than LCO_LLM_COMPRESS_MIN_TOKENS (default: 200).
        if mode in ("medium", "aggressive"):
            llm_comp = get_llm_compressor()
            if await llm_comp.is_available():
                llm_msgs = []
                llm_saved = 0
                last_user_idx = max(
                    (i for i, m in enumerate(messages) if m.get("role") == "user"),
                    default=-1
                )
                for idx, msg in enumerate(messages):
                    if idx == last_user_idx:
                        llm_msgs.append(msg)
                        continue
                    content_val = msg.get("content")
                    if not isinstance(content_val, str) or not content_val:
                        llm_msgs.append(msg)
                        continue
                    compressed_val = await llm_comp.compress(content_val, mode=mode)
                    if compressed_val != content_val:
                        llm_saved += (len(content_val) - len(compressed_val)) // 4
                        llm_msgs.append({**msg, "content": compressed_val})
                    else:
                        llm_msgs.append(msg)
                if llm_saved > 0:
                    messages = llm_msgs
                    optimised_body = {**optimised_body, "messages": messages}
                    record.input_tokens_saved_est += llm_saved
                    logger.info("LLM compress: -%d tokens", llm_saved)

        # ── LCO-4: Input quality gate ─────────────────────────────────────
        if optimised_body is not body:
            orig_text = " ".join(
                str(m.get("content", "")) for m in body.get("messages", [])
                if isinstance(m.get("content"), str)
            )
            new_text = " ".join(
                str(m.get("content", "")) for m in messages
                if isinstance(m.get("content"), str)
            )
            gate = get_quality_gate()
            gate_result = await gate.check(orig_text, new_text)
            record.quality_score = gate_result.score
            if not gate_result.passed:
                logger.warning("Input quality gate rejected (score=%.3f) — reverting",
                               gate_result.score)
                optimised_body = body
                record.input_tokens_saved_est = 0

    upstream_url = adapter.build_url(f"/{path}")
    upstream_headers = adapter.build_headers(headers)
    upstream_body = adapter.normalise_request(optimised_body)
    is_streaming = bool(optimised_body.get("stream", False))

    logger.info("→ %s  model=%s  stream=%s  mode=%s  path=/v1/%s",
                provider_name, record.model, is_streaming, mode, path)

    try:
        if is_streaming:
            stream: AsyncIterator[bytes] = adapter.stream(
                upstream_url, upstream_headers, upstream_body
            )
            return StreamingResponse(
                _streaming_response(stream, record, provider_name, safe_hits,
                                    model=body.get("model", ""), query=query),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "x-lco-provider": provider_name,
                    "x-lco-safe-zones": str(safe_hits),
                    "x-lco-buffer": "enabled",
                    "x-lco-mode": mode,
                    "x-lco-input-saved": str(record.input_tokens_saved_est),
                    "x-lco-output-saved": "0",  # updated post-stream via metrics
                },
            )
        else:
            response = await adapter.complete(upstream_url, upstream_headers, upstream_body)
            resp_body = response.json()
            inp, out, total = _extract_usage(resp_body)
            record.input_tokens = inp
            record.output_tokens = out
            record.total_tokens = total

            # ── Output compression (blocking path) ────────────────────────
            if mode != "passthrough" and settings.output_optimization:
                import copy as _copy
                raw_content = ""
                if resp_body.get("choices"):
                    raw_content = (resp_body["choices"][0].get("message") or {}).get("content") or ""
                if raw_content and not _has_code_or_json(raw_content):
                    compress_fn = make_output_compress_fn(mode, query=query)
                    # Use mixed-content compressor: preserves code blocks,
                    # compresses prose sections only
                    import re as _re
                    has_code = bool(_re.search(r"```", raw_content))
                    if has_code:
                        compressed_content = await _compress_mixed_content(raw_content, compress_fn)
                    else:
                        compressed_content = await compress_fn(raw_content)
                    if compressed_content and compressed_content != raw_content:
                        gate = get_quality_gate()
                        gate_result = await gate.check(raw_content, compressed_content)
                        record.quality_score = gate_result.score
                        if gate_result.passed:
                            out_saved = max(0, (len(raw_content) - len(compressed_content)) // 4)
                            record.output_tokens_saved_est = out_saved
                            resp_body = _copy.deepcopy(resp_body)
                            resp_body["choices"][0]["message"]["content"] = compressed_content
                            logger.debug("Blocking output: saved ~%d tokens (mixed=%s)",
                                         out_saved, has_code)

            logger.info("← %s  tokens=(%s in / %s out)  in_saved=%s  out_saved=%s",
                        provider_name, inp, out,
                        record.input_tokens_saved_est, record.output_tokens_saved_est)

            return JSONResponse(
                content=resp_body,
                status_code=response.status_code,
                headers={
                    "x-lco-provider": provider_name,
                    "x-lco-safe-zones": str(safe_hits),
                    "x-lco-mode": mode,
                    "x-lco-input-saved":  str(record.input_tokens_saved_est),
                    "x-lco-output-saved": str(record.output_tokens_saved_est),
                },
            )

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        record.status_code = status
        logger.warning("Upstream %s: %s", status, exc.response.text[:200])
        try:
            detail = exc.response.json()
        except Exception:
            detail = {"error": exc.response.text}
        return JSONResponse(content=detail, status_code=status)

    except httpx.TimeoutException:
        record.status_code = 504
        logger.error("Upstream timeout after %ss", settings.upstream_timeout)
        return JSONResponse(
            content={"error": {"message": "Upstream timeout", "type": "timeout_error"}},
            status_code=504,
        )

    except Exception as exc:
        record.status_code = 500
        logger.exception("Unexpected proxy error: %s", exc)
        return JSONResponse(
            content={"error": {"message": "Internal proxy error", "type": "internal_error"}},
            status_code=500,
        )


# ── Health & status ───────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": _VERSION}


@router.get("/lco/status")
async def status() -> dict:
    try:
        db = await MetricsDB.get()
        summary = await db.summary(last_n=1000)
    except Exception:
        summary = {}
    return {
        "status": "running",
        "version": _VERSION,
        "compression_mode": settings.compression_mode,
        "output_optimization": settings.output_optimization,
        "memory_compression": settings.memory_compression,
        "memory_window": settings.memory_window,
        "quality_gate_enabled": settings.quality_gate_enabled,
        "quality_threshold": settings.quality_threshold,
        "embedder": getattr(settings, "embedder", "tfidf"),
        "providers_supported": ["openai", "anthropic"],
        "buffer_enabled": True,
        "metrics": summary,
    }


# ── Dashboard & recent requests ───────────────────────────────────────────────

@router.get("/lco/recent")
async def recent() -> list:
    """Return recent requests for the dashboard table."""
    try:
        db = await MetricsDB.get()
        return await db.recent(limit=20)
    except Exception:
        return []


@router.post("/lco/control")
async def control(body: dict) -> dict:
    """
    Runtime config endpoint — change settings without restarting.
    Called by lco_ctl.py. Accepts any subset of config fields.
    """
    changed = {}

    if "compression_mode" in body:
        m = body["compression_mode"]
        if m in {"passthrough", "light", "medium", "aggressive"}:
            settings.compression_mode = m
            changed["compression_mode"] = m

    if "output_optimization" in body:
        v = bool(body["output_optimization"])
        settings.output_optimization = v
        changed["output_optimization"] = v

    if "memory_compression" in body:
        v = bool(body["memory_compression"])
        settings.memory_compression = v
        changed["memory_compression"] = v

    if "memory_window" in body:
        settings.memory_window = int(body["memory_window"])
        changed["memory_window"] = settings.memory_window

    if "quality_gate_enabled" in body:
        v = bool(body["quality_gate_enabled"])
        settings.quality_gate_enabled = v
        changed["quality_gate_enabled"] = v

    if "quality_threshold" in body:
        t = float(body["quality_threshold"])
        settings.quality_threshold = t
        changed["quality_threshold"] = t
        # Also update the live gate singleton
        from ..proxy.quality_gate import reset_quality_gate as _rqg
        _rqg()

    if "embedder" in body:
        e = body["embedder"]
        if e in {"tfidf", "ollama", "null"}:
            settings.embedder = e
            changed["embedder"] = e
            from ..proxy.quality_gate import reset_quality_gate
            reset_quality_gate()

    if "reset_metrics" in body and body["reset_metrics"]:
        db = await MetricsDB.get()
        if db._db:
            await db._db.execute("DELETE FROM requests")
            await db._db.commit()
        changed["metrics_reset"] = True

    logger.info("Runtime config changed: %s", changed)
    return {"ok": True, "changed": changed}


@router.get("/lco/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Serve the local metrics dashboard."""
    from ..proxy.dashboard import DASHBOARD_HTML
    return HTMLResponse(content=DASHBOARD_HTML)