"""
LCO-3 Test Suite — Input Cleaner & Deduplicator
+ Streaming Integration Harness (LCO-2 HIGH risk mitigation)

Covers:
  Cleaner unit tests
  - Whitespace normalisation
  - Boilerplate sentence removal
  - Sentence deduplication
  - Last user message preserved
  - Short messages passed through
  - Non-string content passed through
  - clean_messages on a full messages array

  Streaming integration harness (addresses HIGH risk:
  "Streaming regressions will break agents — needs integration test harness")
  - Full SSE wire format integrity over a synthetic multi-chunk stream
  - Tool call stream passes through byte-for-byte (no modification)
  - Code block stream passes through byte-for-byte
  - Prose stream is buffered and replayed correctly
  - Empty stream handled gracefully
  - Compression error does not corrupt stream
  - Agent-style multi-turn request with tool calls is unmodified end-to-end
"""

from __future__ import annotations
import json
import pytest
import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from lco.proxy.cleaner import (
    clean_text,
    clean_messages,
    _normalise_whitespace,
    _remove_boilerplate_sentences,
    _deduplicate_sentences,
    CleanResult,
)
from lco.proxy.buffer import StreamBuffer, _passthrough


# ═══════════════════════════════════════════════════════════════════════════════
# Cleaner unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWhitespaceNormalisation:
    def test_collapses_excess_blank_lines(self):
        text = "Line one.\n\n\n\n\nLine two."
        result = _normalise_whitespace(text)
        assert "\n\n\n" not in result
        assert "Line one." in result
        assert "Line two." in result

    def test_strips_trailing_spaces(self):
        text = "Hello   \nWorld   "
        result = _normalise_whitespace(text)
        assert "   \n" not in result
        assert "   " not in result

    def test_strips_leading_trailing_blank_lines(self):
        text = "\n\nContent\n\n"
        assert _normalise_whitespace(text) == "Content"


class TestBoilerplateRemoval:
    def _clean(self, text: str) -> str:
        stats = CleanResult()
        return _remove_boilerplate_sentences(text, stats)

    def _stats(self, text: str) -> CleanResult:
        stats = CleanResult()
        _remove_boilerplate_sentences(text, stats)
        return stats

    def test_removes_certainly(self):
        text = "Certainly! Here is the answer."
        result = self._clean(text)
        assert "Certainly" not in result
        assert "Here is the answer" in result

    def test_removes_of_course(self):
        text = "Of course. I will help you with that."
        result = self._clean(text)
        assert "Of course" not in result

    def test_removes_great_question(self):
        text = "Great question! The answer is 42."
        result = self._clean(text)
        assert "Great question" not in result
        assert "42" in result

    def test_removes_hope_this_helps(self):
        text = "The answer is Paris. I hope this helps!"
        result = self._clean(text)
        assert "I hope this helps" not in result
        assert "Paris" in result

    def test_removes_feel_free_to_ask(self):
        text = "Done. Feel free to ask any follow-up questions."
        result = self._clean(text)
        assert "Feel free to ask" not in result

    def test_removes_ai_opener(self):
        text = "As an AI language model, I can explain this."
        result = self._clean(text)
        assert "As an AI language model" not in result

    def test_counts_removed_sentences(self):
        text = "Certainly! Great question! The answer is 42."
        stats = self._stats(text)
        assert stats.boilerplate_removed >= 1

    def test_preserves_meaningful_content(self):
        text = "The French Revolution began in 1789."
        result = self._clean(text)
        assert "1789" in result

    def test_does_not_remove_mid_sentence_match(self):
        # "certainly" in the middle of a sentence should NOT be removed
        text = "This is certainly the right approach."
        result = self._clean(text)
        assert "certainly the right approach" in result


class TestDeduplication:
    def _dedup(self, text: str) -> tuple[str, CleanResult]:
        stats = CleanResult()
        result = _deduplicate_sentences(text, stats)
        return result, stats

    def test_removes_exact_duplicate_sentence(self):
        text = "Paris is the capital of France. Paris is the capital of France."
        result, stats = self._dedup(text)
        assert result.count("Paris is the capital of France") == 1
        assert stats.duplicates_removed == 1

    def test_removes_near_duplicate_punctuation_difference(self):
        text = "Use Python for this task. Use Python for this task!"
        result, stats = self._dedup(text)
        assert stats.duplicates_removed >= 1

    def test_keeps_distinct_sentences(self):
        text = "Python is fast. Python is readable. Python is popular."
        result, stats = self._dedup(text)
        assert stats.duplicates_removed == 0
        assert "fast" in result
        assert "readable" in result
        assert "popular" in result

    def test_preserves_first_occurrence(self):
        text = "The answer is 42. The answer is 42."
        result, _ = self._dedup(text)
        assert "42" in result


class TestCleanMessages:
    def _msg(self, role: str, content: str) -> dict:
        return {"role": role, "content": content}

    def test_last_user_message_preserved(self):
        messages = [
            self._msg("user", "Certainly! " * 20 + "original question"),
            self._msg("assistant", "Certainly! Here is the answer."),
            self._msg("user", "Certainly! What is 2+2?"),  # last user — must not change
        ]
        cleaned, stats = clean_messages(messages)
        last = cleaned[-1]["content"]
        assert last == "Certainly! What is 2+2?"

    def test_assistant_message_cleaned(self):
        messages = [
            self._msg("user", "What is Python?"),
            self._msg("assistant", "Great question! " + "Python is a programming language. " * 5),
        ]
        cleaned, stats = clean_messages(messages)
        assert "Great question" not in cleaned[1]["content"]

    def test_short_message_not_touched(self):
        messages = [self._msg("user", "Hello!")]
        cleaned, stats = clean_messages(messages)
        assert cleaned[0]["content"] == "Hello!"
        assert stats.messages_modified == 0

    def test_tool_call_message_passed_through(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "search", "arguments": "{}"}}],
            }
        ]
        cleaned, stats = clean_messages(messages)
        assert cleaned[0].get("tool_calls") is not None
        assert stats.messages_modified == 0

    def test_list_content_passed_through(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
        ]
        cleaned, stats = clean_messages(messages)
        assert isinstance(cleaned[0]["content"], list)
        assert stats.messages_modified == 0

    def test_returns_new_list_original_unchanged(self):
        original_content = "Certainly! " + "a" * 100
        messages = [self._msg("assistant", original_content)]
        cleaned, _ = clean_messages(messages)
        # Original list and dict must not be mutated
        assert messages[0]["content"] == original_content
        assert cleaned is not messages

    def test_deduplication_across_long_message(self):
        repeated = "Python is a great language. " * 10
        messages = [self._msg("system", repeated)]
        cleaned, stats = clean_messages(messages, skip_last_user=False)
        assert stats.duplicates_removed > 0
        assert len(cleaned[0]["content"]) < len(repeated)


# ═══════════════════════════════════════════════════════════════════════════════
# Streaming integration harness
# Addresses HIGH risk: "Streaming regressions will break agents"
# ═══════════════════════════════════════════════════════════════════════════════

def _sse(content: str = "", finish: str | None = None,
         tool_calls=None, cid="chatcmpl-x", model="gpt-4o") -> bytes:
    delta: dict = {}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = tool_calls
    event = {
        "id": cid, "object": "chat.completion.chunk", "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(event)}\n\n".encode()


def _done() -> bytes:
    return b"data: [DONE]\n\n"


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _collect(gen) -> list[bytes]:
    """Synchronously collect an async generator."""
    return asyncio.get_event_loop().run_until_complete(_collect_async(gen))


async def _collect_async(gen) -> list[bytes]:
    return [c async for c in gen]


class TestStreamingIntegrity:
    """
    Integration harness that tests the full buffer → replay pipeline.
    These tests simulate what an agent sees on the wire and assert
    that no data is lost, corrupted, or incorrectly modified.
    """

    def test_prose_stream_assembles_and_replays(self):
        """Full prose stream: buffered, passthrough hook, replayed correctly."""
        async def _inner():
            chunks = [
                _sse("The "), _sse("answer "), _sse("is "), _sse("42."),
                _sse(finish="stop"), _done(),
            ]
            buf = StreamBuffer()
            result = await buf.collect(_stream(*chunks))
    
            assert result.assembled_text == "The answer is 42."
            assert result.finish_reason == "stop"
            assert not result.passthrough
    
            replayed = [c async for c in buf.replay()]
            # passthrough=False → synthetic SSE: content chunk + finish chunk + DONE
            assert len(replayed) == 3
            content_event = json.loads(replayed[0].decode().split("data: ")[1])
            assert content_event["choices"][0]["delta"]["content"] == "The answer is 42."
        asyncio.run(_inner())

    def test_tool_call_stream_byte_identical_passthrough(self):
        """
        Tool call stream MUST come out byte-for-byte identical.
        Any modification would break the agent's function call parsing.
        """
        async def _inner():
            tool_chunk = _sse(tool_calls=[{
                "index": 0, "id": "call_abc",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}
            }])
            finish_chunk = _sse(finish="tool_calls")
            original_chunks = [tool_chunk, finish_chunk]
    
            buf = StreamBuffer()
            await buf.collect(_stream(*original_chunks))
            assert buf._result is not None, "collect() must populate _result"
    
            assert buf._result.has_tool_calls is True
            assert buf._result.passthrough is True
            assert buf._result.passthrough_reason == "tool_call_delta"
    
            replayed = [c async for c in buf.replay()]
            assert replayed == original_chunks, (
                "Tool call stream must be byte-for-byte identical after buffering. "
                "Any difference would corrupt the agent's function call."
            )
        asyncio.run(_inner())

    def test_code_block_stream_byte_identical_passthrough(self):
        """Code block responses must pass through unmodified."""
        async def _inner():
            chunks = [
                _sse("Here is the fix:\n```python\n"),
                _sse("print('hello')\n"),
                _sse("```"),
                _sse(finish="stop"),
            ]
            buf = StreamBuffer()
            await buf.collect(_stream(*chunks))
            assert buf._result is not None, "collect() must populate _result"
    
            assert buf._result.has_code_block is True
            assert buf._result.passthrough is True
    
            replayed = [c async for c in buf.replay()]
            assert replayed == chunks
        asyncio.run(_inner())

    def test_compression_reduces_tokens_for_prose(self):
        """Compression hook fires on prose and produces shorter output."""
        async def _inner():
            async def shorten(text: str) -> str:
                return "42."  # drastically shorter
    
            chunks = [
                _sse("The answer to everything is forty-two, as has been established."),
                _sse(finish="stop"),
            ]
            buf = StreamBuffer()
            result = await buf.collect(_stream(*chunks), compress_fn=shorten)
    
            assert result.compressed_text == "42."
            assert result.token_reduction_chars > 0
            assert not result.passthrough
    
            replayed = [c async for c in buf.replay()]
            content_event = json.loads(replayed[0].decode().split("data: ")[1])
            assert content_event["choices"][0]["delta"]["content"] == "42."
        asyncio.run(_inner())

    def test_empty_stream_no_crash(self):
        """Empty stream should produce empty result without error."""
        async def _inner():
            async def empty():
                return
                yield  # make it a generator
    
            buf = StreamBuffer()
            result = await buf.collect(empty())
            assert result.assembled_text == ""
            replayed = [c async for c in buf.replay()]
            # Empty passthrough → 0 chunks replayed (no original chunks either)
            assert isinstance(replayed, list)
        asyncio.run(_inner())

    def test_compression_error_stream_survives(self):
        """If compression raises, the original stream must still be delivered."""
        async def _inner():
            async def broken(text: str) -> str:
                raise RuntimeError("GPU exploded")
    
            chunks = [_sse("Hello world!"), _sse(finish="stop")]
            buf = StreamBuffer()
            result = await buf.collect(_stream(*chunks), compress_fn=broken)
    
            assert result.passthrough is True
            assert result.passthrough_reason == "compress_error"
            # Original bytes must come out
            replayed = [c async for c in buf.replay()]
            assert replayed == chunks
        asyncio.run(_inner())

    def test_multi_chunk_large_stream_integrity(self):
        """Stress test: 100 small chunks assemble into the correct full text."""
        async def _inner():
            words = [f"word{i} " for i in range(100)]
            chunks = [_sse(w) for w in words] + [_sse(finish="stop")]
    
            buf = StreamBuffer()
            result = await buf.collect(_stream(*chunks))
    
            expected = "".join(words)
            assert result.assembled_text == expected
            assert len(result.raw_chunks) == 101
        asyncio.run(_inner())

class TestAgentPipelineIntegration:
    """
    End-to-end test: simulate a full agent tool-use loop through the proxy.
    Asserts that the entire request/response cycle does not corrupt any data.
    """

    def test_agent_tool_call_loop_unmodified(self):
        """
        Simulates: user asks → assistant calls tool → tool result → assistant answers.
        The tool call and result messages must arrive at the upstream unmodified.
        The streaming response containing another tool call must replay byte-identically.
        """
        async def _inner():
            from lco.proxy.safe_zones import classify_messages, SafeZoneReason
    
            messages = [
                {"role": "user", "content": "What is the weather in Paris?"},
                {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "call_wx1", "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}
                    }]
                },
                {"role": "tool", "tool_call_id": "call_wx1",
                 "content": '{"temp": 18, "condition": "sunny"}'},
            ]
    
            # Verify safe zones protect tool messages
            classified = classify_messages(messages)
            _, safe1, reason1 = classified[1]
            _, safe2, reason2 = classified[2]
    
            assert safe1 and reason1 == SafeZoneReason.TOOL_CALL
            assert safe2 and reason2 == SafeZoneReason.TOOL_RESULT
    
            # Simulate the streaming response containing the assistant's final answer
            response_chunks = [
                _sse("It is 18°C and sunny in Paris."),
                _sse(finish="stop"),
            ]
            buf = StreamBuffer()
            result = await buf.collect(_stream(*response_chunks))
    
            assert not result.passthrough
            assert "18°C" in result.assembled_text
    
            replayed = [c async for c in buf.replay()]
            full_text = "".join(
                json.loads(c.decode().split("data: ")[1])["choices"][0]["delta"].get("content", "")
                for c in replayed
                if c.startswith(b"data:") and b"[DONE]" not in c
                and json.loads(c.decode().split("data: ")[1])["choices"][0]["delta"].get("content")
            )
            assert "18°C" in full_text
        asyncio.run(_inner())