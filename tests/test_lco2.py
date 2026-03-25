"""
LCO-2 Test Suite — Streaming-Safe Output Buffer
================================================
Covers:
  - SSE chunk parsing and text assembly
  - Tool call delta → forced passthrough
  - Code block → forced passthrough
  - Passthrough: original bytes re-emitted verbatim
  - Compression hook: compressed text re-emitted as synthetic SSE
  - Compression hook error → fallback to passthrough
  - Multiple chunks assembled correctly
  - Proxy status endpoint reports buffer_enabled
"""

from __future__ import annotations
import json
import pytest
import asyncio
from typing import AsyncIterator

from lco.proxy.buffer import (
    StreamBuffer,
    BufferResult,
    _passthrough,
    _parse_sse_chunk,
    _extract_delta_text,
    _has_tool_call_delta,
    _get_finish_reason,
)


# ── SSE parsing helpers ───────────────────────────────────────────────────────

class TestSSEParsing:
    def _make_chunk(self, content: str, finish_reason=None, tool_calls=None) -> bytes:
        delta: dict = {}
        if content:
            delta["content"] = content
        if tool_calls:
            delta["tool_calls"] = tool_calls
        choice: dict = {"index": 0, "delta": delta, "finish_reason": finish_reason}
        event = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "model": "gpt-4o",
            "choices": [choice],
        }
        return f"data: {json.dumps(event)}\n\n".encode()

    def test_parse_text_delta(self):
        chunk = self._make_chunk("Hello")
        events = _parse_sse_chunk(chunk)
        assert len(events) == 1
        assert _extract_delta_text(events[0]) == "Hello"

    def test_parse_done_skipped(self):
        raw = b"data: [DONE]\n\n"
        events = _parse_sse_chunk(raw)
        assert events == []

    def test_parse_empty_line_skipped(self):
        raw = b"\n\n"
        events = _parse_sse_chunk(raw)
        assert events == []

    def test_parse_non_data_line_skipped(self):
        raw = b"event: ping\n\n"
        events = _parse_sse_chunk(raw)
        assert events == []

    def test_has_tool_call_delta_true(self):
        event = {
            "choices": [{"delta": {"tool_calls": [{"id": "call_1"}]}}]
        }
        assert _has_tool_call_delta(event)

    def test_has_tool_call_delta_false(self):
        event = {"choices": [{"delta": {"content": "hello"}}]}
        assert not _has_tool_call_delta(event)

    def test_get_finish_reason(self):
        event = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        assert _get_finish_reason(event) == "stop"

    def test_get_finish_reason_none(self):
        event = {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]}
        assert _get_finish_reason(event) is None

    def test_multiple_chunks_in_one_payload(self):
        e1 = {"id": "x", "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}
        e2 = {"id": "x", "choices": [{"delta": {"content": " world"}, "finish_reason": None}]}
        raw = f"data: {json.dumps(e1)}\n\ndata: {json.dumps(e2)}\n\n".encode()
        events = _parse_sse_chunk(raw)
        assert len(events) == 2
        assert _extract_delta_text(events[0]) == "Hello"
        assert _extract_delta_text(events[1]) == " world"


# ── Async helpers ─────────────────────────────────────────────────────────────

async def _make_stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def _text_chunk(text: str, cid: str = "chatcmpl-test", model: str = "gpt-4o") -> bytes:
    event = {
        "id": cid,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(event)}\n\n".encode()


def _finish_chunk(reason: str = "stop", cid: str = "chatcmpl-test", model: str = "gpt-4o") -> bytes:
    event = {
        "id": cid,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
    }
    return f"data: {json.dumps(event)}\n\n".encode()


def _tool_call_chunk(cid: str = "chatcmpl-test") -> bytes:
    event = {
        "id": cid,
        "object": "chat.completion.chunk",
        "model": "gpt-4o",
        "choices": [{"index": 0, "delta": {
            "tool_calls": [{"index": 0, "id": "call_1",
                            "function": {"name": "search", "arguments": '{"q":'}}]
        }, "finish_reason": None}],
    }
    return f"data: {json.dumps(event)}\n\n".encode()


async def _collect_replay(buf: StreamBuffer) -> list[bytes]:
    chunks = []
    async for chunk in buf.replay():
        chunks.append(chunk)
    return chunks


# ── Buffer collection tests ───────────────────────────────────────────────────

class TestBufferCollection:
    def test_assembles_text_from_multiple_chunks(self):
        async def _inner():
            stream = _make_stream(
                _text_chunk("Hello"),
                _text_chunk(", "),
                _text_chunk("world!"),
                _finish_chunk(),
            )
            buf = StreamBuffer()
            result = await buf.collect(stream)
            assert result.assembled_text == "Hello, world!"
            assert result.finish_reason == "stop"
            assert not result.has_tool_calls
            assert not result.passthrough
        asyncio.run(_inner())

    def test_captures_completion_id_and_model(self):
        async def _inner():
            stream = _make_stream(
                _text_chunk("Hi", cid="chatcmpl-abc", model="gpt-4o-mini"),
                _finish_chunk(cid="chatcmpl-abc", model="gpt-4o-mini"),
            )
            buf = StreamBuffer()
            result = await buf.collect(stream, model="gpt-4o-mini")
            assert result.completion_id == "chatcmpl-abc"
            assert result.model == "gpt-4o-mini"
        asyncio.run(_inner())

    def test_empty_stream_produces_empty_text(self):
        async def _inner():
            async def empty_stream():
                return
                yield  # make it an async generator
            buf = StreamBuffer()
            result = await buf.collect(empty_stream())
            assert result.assembled_text == ""
            assert result.passthrough is False
        asyncio.run(_inner())

    def test_passthrough_hook_leaves_text_unchanged(self):
        async def _inner():
            stream = _make_stream(_text_chunk("Test"), _finish_chunk())
            buf = StreamBuffer()
            result = await buf.collect(stream, compress_fn=_passthrough)
            assert result.compressed_text == "Test"
            assert result.token_reduction_chars == 0
        asyncio.run(_inner())

    def test_compression_hook_applied_to_prose(self):
        async def _inner():
            async def shorten(text: str) -> str:
                return "Short."
    
            stream = _make_stream(
                _text_chunk("This is a very long response that goes on and on."),
                _finish_chunk(),
            )
            buf = StreamBuffer()
            result = await buf.collect(stream, compress_fn=shorten)
            assert result.compressed_text == "Short."
            assert result.token_reduction_chars > 0
            assert not result.passthrough
        asyncio.run(_inner())

    def test_compression_error_falls_back_to_passthrough(self):
        async def _inner():
            async def broken(text: str) -> str:
                raise RuntimeError("compression exploded")
    
            stream = _make_stream(_text_chunk("Hello"), _finish_chunk())
            buf = StreamBuffer()
            result = await buf.collect(stream, compress_fn=broken)
            assert result.passthrough is True
            assert result.passthrough_reason == "compress_error"
            assert result.compressed_text == "Hello"
        asyncio.run(_inner())

# ── Safe Zone passthrough tests ───────────────────────────────────────────────

class TestBufferSafeZones:
    def test_tool_call_delta_forces_passthrough(self):
        async def _inner():
            stream = _make_stream(
                _tool_call_chunk(),
                _finish_chunk(reason="tool_calls"),
            )
            buf = StreamBuffer()
            result = await buf.collect(stream)
            assert result.has_tool_calls is True
            assert result.passthrough is True
            assert result.passthrough_reason == "tool_call_delta"
        asyncio.run(_inner())

    def test_code_block_forces_passthrough(self):
        async def _inner():
            code_response = "Here is the fix:\n```python\nprint('hello')\n```"
            stream = _make_stream(_text_chunk(code_response), _finish_chunk())
            buf = StreamBuffer()
    
            async def shorten(text: str) -> str:
                return "COMPRESSED"
    
            result = await buf.collect(stream, compress_fn=shorten)
            assert result.has_code_block is True
            assert result.passthrough is True
            assert result.passthrough_reason == "code_block"
            # Even though we passed a compression hook, it must not have been applied
            assert result.compressed_text == code_response
        asyncio.run(_inner())

    def test_prose_without_code_block_is_compressible(self):
        async def _inner():
            stream = _make_stream(
                _text_chunk("The answer is forty-two."),
                _finish_chunk(),
            )
            buf = StreamBuffer()
            result = await buf.collect(stream)
            assert not result.has_code_block
            assert not result.passthrough
        asyncio.run(_inner())

# ── Replay tests ──────────────────────────────────────────────────────────────

class TestBufferReplay:
    def test_passthrough_replays_original_bytes_verbatim(self):
        async def _inner():
            raw_chunks = [
                _tool_call_chunk(),
                _finish_chunk(reason="tool_calls"),
            ]
    
            async def stream():
                for c in raw_chunks:
                    yield c
    
            buf = StreamBuffer()
            await buf.collect(stream())
            replayed = await _collect_replay(buf)
    
            # Must get back exactly the same bytes
            assert replayed == raw_chunks
        asyncio.run(_inner())

    def test_compressed_replay_emits_synthetic_sse(self):
        async def _inner():
            async def shorten(text: str) -> str:
                return "Brief."
    
            stream = _make_stream(
                _text_chunk("This is a very verbose answer that should be compressed."),
                _finish_chunk(),
            )
            buf = StreamBuffer()
            await buf.collect(stream, compress_fn=shorten)
            replayed = await _collect_replay(buf)
    
            # Should have: 1 content chunk + 1 finish chunk + [DONE]
            assert len(replayed) == 3
    
            content_event = json.loads(replayed[0].decode().replace("data: ", "").strip())
            assert content_event["choices"][0]["delta"]["content"] == "Brief."
    
            finish_event = json.loads(replayed[1].decode().replace("data: ", "").strip())
            assert finish_event["choices"][0]["finish_reason"] == "stop"
    
            assert replayed[2] == b"data: [DONE]\n\n"
        asyncio.run(_inner())

    def test_replay_before_collect_raises(self):
        async def _inner():
            buf = StreamBuffer()
            with pytest.raises(RuntimeError, match="collect"):
                async for _ in buf.replay():
                    pass
        asyncio.run(_inner())

    def test_code_block_passthrough_replays_original(self):
        async def _inner():
            original_chunk = _text_chunk("```python\nprint(1)\n```")
            finish = _finish_chunk()
    
            async def stream():
                yield original_chunk
                yield finish
    
            buf = StreamBuffer()
            await buf.collect(stream())
            replayed = await _collect_replay(buf)
            assert original_chunk in replayed
            assert finish in replayed
        asyncio.run(_inner())

# ── Integration: buffer wired into proxy router ───────────────────────────────

from fastapi.testclient import TestClient
from lco.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestBufferIntegration:
    def test_status_reports_buffer_enabled(self, client):
        r = client.get("/lco/status")
        assert r.status_code == 200
        assert r.json().get("buffer_enabled") is True

    def test_streaming_response_has_buffer_header(self, client):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def mock_stream(*args, **kwargs):
            yield _text_chunk("Hello from buffer!")
            yield _finish_chunk()

        with patch("lco.proxy.router.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.stream = MagicMock(return_value=MagicMock(
                __aenter__=AsyncMock(return_value=MagicMock(
                    raise_for_status=MagicMock(),
                    aiter_bytes=mock_stream,
                )),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_get_client.return_value = mock_client

            r = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
                headers={"Authorization": "Bearer sk-test"},
            )

        assert r.headers.get("x-lco-buffer") == "enabled"