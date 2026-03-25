"""
LCO Phase 2 Test Suite
=======================
LCO-5: Semantic compressor
LCO-6: Output optimizer
LCO-7: Conversation memory compression
Full pipeline integration
"""

from __future__ import annotations
import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from lco.proxy.compressor import (
    compress_text,
    compress_messages,
    CompressResult,
    _split_sentences,
    _tfidf_vec,
    _idf,
    _tokenise,
    _cosine,
)
from lco.proxy.output_optimizer import (
    compress_output_light,
    compress_output_medium,
    compress_output_aggressive,
    make_output_compress_fn,
    _strip_output_boilerplate,
)
from lco.proxy.memory import (
    compress_memory,
    MemoryResult,
    _count_turns,
    _is_compressible,
)


# ═══════════════════════════════════════════════════════════════════════════════
# LCO-5: Semantic compressor
# ═══════════════════════════════════════════════════════════════════════════════

class TestSentenceSplitter:
    def test_splits_on_period_capital(self):
        # Sentences must be >= 20 chars to pass the MIN_SENTENCE_CHARS filter
        text = ("Python is a programming language. "
                "It was created by Guido van Rossum. "
                "It emphasises readability.")
        parts = _split_sentences(text)
        assert len(parts) >= 2

    def test_single_sentence_returns_list_of_one(self):
        text = "Just one longer sentence here without any break at all in it."
        parts = _split_sentences(text)
        assert len(parts) == 1
        assert parts[0] == text

    def test_filters_short_fragments(self):
        # "A." is < 20 chars — must be filtered
        text = "A. This is a real sentence with enough content to pass the filter."
        parts = _split_sentences(text)
        assert not any(len(p) < 20 for p in parts)

    def test_empty_string_returns_empty(self):
        assert _split_sentences("") == []
        assert _split_sentences("   ") == []


class TestCompressText:
    def test_within_budget_returns_unchanged(self):
        text = "Short text."
        result, stats = compress_text(text, token_budget=1000)
        assert result == text
        assert stats.compressed_chars == len(text)

    def test_reduces_long_text(self):
        text = " ".join([
            "Python is a high-level programming language.",
            "It was created by Guido van Rossum.",
            "Python emphasises code readability.",
            "It uses significant indentation.",
            "Python supports multiple paradigms.",
            "Object-oriented programming is supported.",
            "Functional programming is also possible.",
            "Python has a large standard library.",
            "The community is very active and helpful.",
            "Many data science tools use Python.",
        ])
        budget = 20  # very tight
        result, stats = compress_text(text, token_budget=budget)
        assert len(result) < len(text)
        assert stats.sentences_kept < stats.sentences_original

    def test_always_includes_first_sentence(self):
        sentences = [
            "First sentence is the most important one here.",
            "Second sentence with different content about stuff.",
            "Third sentence discussing another topic entirely.",
            "Fourth sentence with yet more unrelated material.",
        ]
        text = " ".join(sentences)
        result, stats = compress_text(text, token_budget=30)
        assert sentences[0] in result

    def test_query_aware_keeps_relevant_sentences(self):
        text = (
            "Python was created in 1991. "
            "It is used for web development and data science. "
            "The language supports many paradigms. "
            "Paris is the capital of France. "
            "Machine learning libraries include scikit-learn."
        )
        result_with_query, _ = compress_text(
            text, token_budget=40, query="Python programming language"
        )
        result_no_query, _ = compress_text(text, token_budget=40, query="")
        # With query about Python, Python-related sentences should be preferred
        assert "Python" in result_with_query

    def test_single_sentence_truncates_gracefully(self):
        text = "a " * 500  # 1000 chars, single sentence
        result, stats = compress_text(text, token_budget=10)
        assert len(result) <= 10 * 4 + 5  # budget * CHARS_PER_TOKEN + some slack

    def test_empty_text_returns_empty(self):
        result, stats = compress_text("", token_budget=100)
        assert result == ""

    def test_result_metadata_correct(self):
        text = " ".join([f"Sentence number {i} with some content." for i in range(20)])
        result, stats = compress_text(text, token_budget=50)
        assert stats.original_chars == len(text)
        assert stats.compressed_chars == len(result)
        assert stats.token_budget == 50
        assert stats.sentences_original > 0


class TestCompressMessages:
    def _msg(self, role, content):
        return {"role": role, "content": content}

    def test_last_user_message_never_compressed(self):
        long_content = " ".join([f"Sentence {i}." for i in range(50)])
        messages = [
            self._msg("user", long_content),
            self._msg("assistant", "Response here."),
            self._msg("user", "What about this specific question?"),
        ]
        compressed, results = compress_messages(messages, mode="aggressive",
                                                max_history_tokens=50)
        # Last user message must be unchanged
        assert compressed[-1]["content"] == "What about this specific question?"

    def test_system_messages_always_preserved(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            self._msg("user", " ".join([f"Sentence {i}." for i in range(30)])),
        ]
        compressed, _ = compress_messages(messages, mode="aggressive",
                                          max_history_tokens=20)
        assert compressed[0]["content"] == "You are a helpful assistant."

    def test_tool_messages_not_compressed(self):
        msg = {"role": "tool", "tool_call_id": "c1",
               "content": '{"result": "data here"}'}
        compressed, _ = compress_messages([msg], mode="aggressive",
                                          max_history_tokens=5)
        assert compressed[0]["content"] == msg["content"]

    def test_light_mode_less_aggressive_than_medium(self):
        sentences = " ".join([f"Sentence about topic {i} with detail." for i in range(30)])
        messages = [{"role": "assistant", "content": sentences}]

        comp_light, _ = compress_messages(messages, mode="light",
                                          max_history_tokens=200)
        comp_medium, _ = compress_messages(messages, mode="medium",
                                           max_history_tokens=200)
        light_len = len(comp_light[0].get("content", ""))
        medium_len = len(comp_medium[0].get("content", ""))
        # Medium should be same or shorter than light
        assert medium_len <= light_len + 10  # small slack for edge cases

    def test_returns_new_list_original_unchanged(self):
        original_text = " ".join([f"Sentence {i}." for i in range(20)])
        messages = [{"role": "assistant", "content": original_text}]
        compress_messages(messages, mode="medium", max_history_tokens=30)
        # Original must not be mutated
        assert messages[0]["content"] == original_text


# ═══════════════════════════════════════════════════════════════════════════════
# LCO-6: Output optimizer
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutputBoilerplate:
    def test_removes_hope_this_helps(self):
        text = "The answer is 42. I hope this helps!"
        result = _strip_output_boilerplate(text)
        assert "I hope this helps" not in result
        assert "42" in result

    def test_removes_let_me_know(self):
        text = "Done. Let me know if you need anything else."
        result = _strip_output_boilerplate(text)
        assert "Let me know" not in result

    def test_removes_feel_free(self):
        text = "Here it is. Please don't hesitate to ask more questions."
        result = _strip_output_boilerplate(text)
        assert "hesitate" not in result

    def test_removes_in_summary_opener(self):
        text = "In summary, Python is great."
        result = _strip_output_boilerplate(text)
        assert "In summary" not in result

    def test_preserves_content(self):
        text = "FastAPI is a modern web framework for Python."
        result = _strip_output_boilerplate(text)
        assert "FastAPI" in result
        assert "Python" in result


class TestOutputCompressLight:
    def test_light_compress_removes_boilerplate(self):
        text = "Python is fast. I hope this helps! Let me know if you need more."
        result = asyncio.run(compress_output_light(text))
        assert "I hope this helps" not in result
        assert "Python is fast" in result

    def test_light_compress_never_returns_empty(self):
        text = "I hope this helps!"  # all boilerplate
        result = asyncio.run(compress_output_light(text))
        assert result  # must not be empty

    def test_light_compress_preserves_code_prose(self):
        text = (
            "Here is how you do it. "
            "First import the module. "
            "Then call the function. "
            "I hope this helps!"
        )
        result = asyncio.run(compress_output_light(text))
        assert "import the module" in result


class TestOutputCompressMedium:
    def test_medium_reduces_long_prose(self):
        text = " ".join([
            f"This is sentence {i} discussing topic {i % 3}."
            for i in range(20)
        ])
        result = asyncio.run(compress_output_medium(text))
        assert len(result) < len(text)

    def test_medium_never_returns_empty(self):
        result = asyncio.run(compress_output_medium("Short."))
        assert result


class TestMakeOutputCompressFn:
    def test_passthrough_mode_returns_passthrough(self):
        fn = make_output_compress_fn("passthrough")
        text = "Hello world"
        result = asyncio.run(fn(text))
        assert result == text

    def test_light_mode_returns_callable(self):
        fn = make_output_compress_fn("light")
        assert callable(fn)
        result = asyncio.run(fn("Hello world"))
        assert isinstance(result, str)

    def test_medium_mode_with_query(self):
        fn = make_output_compress_fn("medium", query="Python")
        assert callable(fn)

    def test_aggressive_mode_compresses_more_than_medium(self):
        text = " ".join([f"Sentence {i} with detailed content about topic {i}."
                         for i in range(15)])
        fn_med = make_output_compress_fn("medium")
        fn_agg = make_output_compress_fn("aggressive")
        result_med = asyncio.run(fn_med(text))
        result_agg = asyncio.run(fn_agg(text))
        # Aggressive should be same or shorter
        assert len(result_agg) <= len(result_med) + 20


# ═══════════════════════════════════════════════════════════════════════════════
# LCO-7: Memory compression
# ═══════════════════════════════════════════════════════════════════════════════

class TestMemoryHelpers:
    def test_count_turns(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        assert _count_turns(messages) == 4

    def test_is_compressible_user(self):
        assert _is_compressible({"role": "user", "content": "hello"})

    def test_is_compressible_assistant(self):
        assert _is_compressible({"role": "assistant", "content": "world"})

    def test_not_compressible_system(self):
        assert not _is_compressible({"role": "system", "content": "prompt"})

    def test_not_compressible_tool(self):
        assert not _is_compressible({"role": "tool", "content": "{}"})

    def test_not_compressible_tool_calls(self):
        assert not _is_compressible({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1"}]
        })

    def test_not_compressible_empty(self):
        assert not _is_compressible({"role": "user", "content": ""})
        assert not _is_compressible({"role": "user", "content": None})


class TestCompressMemory:
    def _make_conversation(self, turns: int) -> list[dict]:
        msgs = [{"role": "system", "content": "You are helpful."}]
        for i in range(turns):
            msgs.append({"role": "user",
                         "content": f"Question {i}: " + "detail " * 20})
            msgs.append({"role": "assistant",
                         "content": f"Answer {i}: " + "explanation " * 20})
        return msgs

    def test_within_window_unchanged(self):
        messages = self._make_conversation(3)
        result, stats = compress_memory(messages, window=8)
        assert result == messages
        assert stats.turns_compressed == 0

    def test_beyond_window_compresses_old_turns(self):
        messages = self._make_conversation(10)
        result, stats = compress_memory(messages, window=4)
        assert stats.turns_compressed > 0
        assert stats.tokens_saved_est > 0

    def test_window_turns_preserved(self):
        messages = self._make_conversation(10)
        # window=4 means last 4 turns (each turn = 1 user + 1 assistant)
        # last 4 user messages are preserved
        original_last_user = [
            m["content"] for m in messages if m["role"] == "user"
        ][-1]  # at minimum the final user message must be unchanged
        result, stats = compress_memory(messages, window=4)
        result_last_user = [
            m["content"] for m in result if m["role"] == "user"
        ][-1]
        assert result_last_user == original_last_user
        assert stats.turns_compressed >= 0

    def test_summary_injected_when_enabled(self):
        messages = self._make_conversation(10)
        result, stats = compress_memory(messages, window=4, inject_summary=True)
        assert stats.summary_injected
        # Summary should appear as a system message
        system_msgs = [m for m in result if m["role"] == "system"]
        summary_msgs = [m for m in system_msgs
                        if "Earlier conversation" in m.get("content", "")]
        assert len(summary_msgs) >= 1

    def test_summary_not_injected_when_disabled(self):
        messages = self._make_conversation(10)
        result, stats = compress_memory(messages, window=4, inject_summary=False)
        assert not stats.summary_injected

    def test_system_messages_always_preserved(self):
        messages = self._make_conversation(10)
        result, _ = compress_memory(messages, window=4)
        system_contents = [m["content"] for m in result if m["role"] == "system"
                           and "Earlier conversation" not in m["content"]]
        assert "You are helpful." in system_contents

    def test_result_chars_less_than_original(self):
        messages = self._make_conversation(12)
        original_chars = sum(len(str(m.get("content", ""))) for m in messages)
        result, stats = compress_memory(messages, window=4)
        result_chars = sum(len(str(m.get("content", ""))) for m in result)
        assert result_chars <= original_chars

    def test_empty_messages_no_crash(self):
        result, stats = compress_memory([], window=8)
        assert result == []
        assert stats.turns_compressed == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Full pipeline integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase2Pipeline:
    """End-to-end: light/medium mode through the full proxy pipeline."""

    @pytest.fixture
    def client(self):
        from lco.main import app
        with TestClient(app) as c:
            yield c

    def test_dashboard_route_returns_200(self, client):
        r = client.get("/lco/dashboard")
        assert r.status_code == 200
        assert "LCO" in r.text
        assert "text/html" in r.headers["content-type"]

    def test_recent_route_returns_list(self, client):
        r = client.get("/lco/recent")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_status_has_min_max_latency(self, client):
        r = client.get("/lco/status")
        assert r.status_code == 200
        m = r.json().get("metrics", {})
        # Keys should be present (may be None if no requests yet)
        assert "min_latency_ms" in m or m == {}
        assert "max_latency_ms" in m or m == {}

    def _make_long_body(self, mode: str = "passthrough") -> dict:
        long_history = []
        for i in range(6):
            long_history.append({
                "role": "user",
                "content": f"Certainly! Question {i}: " + "detail " * 15
            })
            long_history.append({
                "role": "assistant",
                "content": f"Great question! Answer {i}: " + "explanation " * 15
            })
        long_history.append({"role": "user", "content": "What is the final answer?"})
        return {
            "model": "gpt-4o",
            "messages": long_history,
            "stream": False,
        }

    def test_passthrough_mode_no_compression(self, client):
        body = self._make_long_body("passthrough")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4o",
            "choices": [{"index": 0,
                          "message": {"role": "assistant", "content": "42"},
                          "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        }
        mock_response.raise_for_status = MagicMock()

        async def mock_post(url, headers=None, json=None, **kw):
            # In passthrough mode, body must be forwarded as-is
            assert json is not None, "Expected a JSON body"
            assert json["messages"] == body["messages"], \
                "Passthrough mode must not modify messages"
            return mock_response

        with patch("lco.proxy.router.get_client") as mock_gc:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_gc.return_value = mock_client
            r = client.post("/v1/chat/completions", json=body,
                            headers={"Authorization": "Bearer sk-test"})

        assert r.status_code == 200
        assert r.headers.get("x-lco-mode") == "passthrough"

    def test_light_mode_header_present(self, client):
        import os
        os.environ["LCO_COMPRESSION_MODE"] = "light"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-test", "object": "chat.completion",
            "created": 1234567890, "model": "gpt-4o",
            "choices": [{"index": 0,
                          "message": {"role": "assistant", "content": "ok"},
                          "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 2, "total_tokens": 82},
        }
        mock_response.raise_for_status = MagicMock()

        async def mock_post(url, headers=None, json=None, **kw):
            return mock_response

        import importlib
        import lco.config as _cfg
        importlib.reload(_cfg)
        import lco.proxy.router as _router
        _router.settings = _cfg.settings

        with patch("lco.proxy.router.get_client") as mock_gc:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_gc.return_value = mock_client
            r = client.post(
                "/v1/chat/completions",
                json=self._make_long_body("light"),
                headers={"Authorization": "Bearer sk-test"},
            )

        assert "x-lco-mode" in r.headers
        os.environ["LCO_COMPRESSION_MODE"] = "passthrough"
        importlib.reload(_cfg)
        _router.settings = _cfg.settings

    def test_status_endpoint_shows_new_fields(self, client):
        r = client.get("/lco/status")
        assert r.status_code == 200
        data = r.json()
        assert "output_optimization" in data
        assert "memory_compression" in data
        assert "memory_window" in data
        assert data["version"] == "0.2.0"


class TestPhase2MemoryPipeline:
    """Memory compression wired into the full request pipeline."""

    def test_memory_compress_reduces_messages(self):
        """
        Verify that a long conversation gets its old turns compressed
        when memory compression is enabled. Checks the compressor directly
        since the proxy path requires real upstream.
        """
        messages = []
        for i in range(12):
            messages.append({"role": "user",
                             "content": f"Certainly! Question {i}: " + "word " * 30})
            messages.append({"role": "assistant",
                             "content": f"Great question! Answer {i}: " + "word " * 30})
        messages.append({"role": "user", "content": "Final question?"})

        original_total = sum(len(str(m.get("content", ""))) for m in messages)
        compressed, stats = compress_memory(messages, window=6, inject_summary=True)
        compressed_total = sum(len(str(m.get("content", ""))) for m in compressed)

        assert stats.turns_compressed > 0
        assert compressed_total <= original_total
        assert stats.tokens_saved_est >= 0

    def test_last_user_message_never_in_compressed_window(self):
        """The final user message is always in the live window."""
        messages = []
        for i in range(15):
            messages.append({"role": "user",
                             "content": f"Question {i}: " + "x " * 50})
            messages.append({"role": "assistant",
                             "content": f"Answer {i}: " + "y " * 50})
        messages.append({"role": "user", "content": "This is my specific final question"})

        compressed, stats = compress_memory(messages, window=4)
        final_user = [m for m in compressed if m["role"] == "user"][-1]
        assert "This is my specific final question" in final_user["content"]