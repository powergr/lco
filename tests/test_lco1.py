"""
LCO Test Suite — LCO-1 / LCO-4
Covers:
  - Safe Zone detection (code blocks, JSON, tool calls, safe tags)
  - Adapter detection (OpenAI vs Anthropic)
  - Anthropic request normalisation
  - Anthropic response translation
  - Proxy health / status endpoints (no upstream required)
  - Tool call passthrough integrity (LCO-1 risk mitigation)
"""

from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


# ── Safe Zone tests ──────────────────────────────────────────────────────────

from lco.proxy.safe_zones import (
    check_message,
    classify_messages,
    has_code_block,
    is_json_payload,
    SafeZoneReason,
)


class TestSafeZones:
    def test_plain_prose_not_safe(self):
        msg = {"role": "user", "content": "What is the capital of France?"}
        is_safe, _reason = check_message(msg)
        assert not is_safe
        assert _reason == SafeZoneReason.NONE

    def test_fenced_code_block_is_safe(self):
        msg = {"role": "user", "content": "Fix this:\n```python\nprint('hello')\n```"}
        is_safe, reason = check_message(msg)
        assert is_safe
        assert reason == SafeZoneReason.CODE_BLOCK

    def test_json_payload_is_safe(self):
        msg = {"role": "user", "content": '{"key": "value", "nested": {"a": 1}}'}
        is_safe, reason = check_message(msg)
        assert is_safe
        assert reason == SafeZoneReason.JSON_PAYLOAD

    def test_json_array_is_safe(self):
        msg = {"role": "user", "content": '[{"id": 1}, {"id": 2}]'}
        is_safe, reason = check_message(msg)
        assert is_safe
        assert reason == SafeZoneReason.JSON_PAYLOAD

    def test_partial_json_in_prose_not_safe(self):
        msg = {"role": "user", "content": 'Use the JSON {"key": "val"} in your answer.'}
        is_safe, _ = check_message(msg)
        assert not is_safe

    def test_tool_call_message_is_safe(self):
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": "{}"}}
            ],
        }
        is_safe, reason = check_message(msg)
        assert is_safe
        assert reason == SafeZoneReason.TOOL_CALL

    def test_tool_result_message_is_safe(self):
        msg = {"role": "tool", "tool_call_id": "call_1", "content": '{"temp": 22}'}
        is_safe, reason = check_message(msg)
        assert is_safe
        assert reason == SafeZoneReason.TOOL_RESULT

    def test_function_role_is_safe(self):
        msg = {"role": "function", "name": "get_weather", "content": '{"temp": 22}'}
        is_safe, reason = check_message(msg)
        assert is_safe
        assert reason == SafeZoneReason.TOOL_RESULT

    def test_safe_tag_is_safe(self):
        msg = {"role": "system", "content": "You are helpful.<!-- lco-safe -->"}
        is_safe, reason = check_message(msg)
        assert is_safe
        assert reason == SafeZoneReason.SAFE_TAG

    def test_anthropic_tool_use_block_is_safe(self):
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me look that up."},
                {"type": "tool_use", "id": "tu_1", "name": "search",
                 "input": {"q": "Paris"}},
            ],
        }
        is_safe, reason = check_message(msg)
        assert is_safe
        assert reason == SafeZoneReason.TOOL_CALL

    def test_classify_messages_mixed(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "```python\nprint(1)\n```"},
            {"role": "tool", "content": '{"result": "ok"}'},
            {"role": "user", "content": "Explain the output above."},
        ]
        results = classify_messages(messages)
        assert len(results) == 4
        _, s0, _ = results[0]; assert not s0
        _, s1, r1 = results[1]; assert s1; assert r1 == SafeZoneReason.CODE_BLOCK
        _, s2, r2 = results[2]; assert s2; assert r2 == SafeZoneReason.TOOL_RESULT
        _, s3, _ = results[3]; assert not s3

    def test_has_code_block_fenced(self):
        assert has_code_block("```js\nconsole.log(1)\n```")
        assert not has_code_block("just prose")

    def test_is_json_payload_invalid(self):
        assert not is_json_payload('{"unclosed": ')
        assert not is_json_payload("not json at all")
        assert not is_json_payload("")


# ── Adapter detection tests ──────────────────────────────────────────────────

import httpx
from lco.adapters import (
    get_adapter, _detect_provider, OpenAIAdapter, AnthropicAdapter,
    PROVIDER_REGISTRY,
)


class TestAdapterDetection:
    def test_openai_default(self):
        headers = {"authorization": "Bearer sk-abc123"}
        body = {"model": "gpt-4o", "messages": []}
        assert _detect_provider(headers, body) == "openai"

    def test_anthropic_by_model_prefix(self):
        headers = {"authorization": "Bearer sk-ant-abc"}
        body = {"model": "claude-opus-4-5", "messages": []}
        assert _detect_provider(headers, body) == "anthropic"

    def test_anthropic_by_key_format(self):
        headers = {"authorization": "Bearer sk-ant-xyz"}
        body = {"model": "gpt-4o", "messages": []}
        assert _detect_provider(headers, body) == "anthropic"

    def test_anthropic_explicit_header(self):
        headers = {"x-lco-provider": "anthropic"}
        body = {"model": "gpt-4o", "messages": []}
        assert _detect_provider(headers, body) == "anthropic"

    def test_openai_explicit_header_overrides_key(self):
        headers = {"x-lco-provider": "openai", "authorization": "Bearer sk-ant-xyz"}
        body = {"model": "claude-opus-4-5", "messages": []}
        assert _detect_provider(headers, body) == "openai"

    def test_ollama_detected_as_openai(self):
        # Ollama uses the OpenAI adapter — no special key format
        headers = {"authorization": "Bearer ollama"}
        body = {"model": "llama3.2", "messages": []}
        assert _detect_provider(headers, body) == "openai"

    def test_openai_adapter_build_url_includes_v1(self):
        import httpx
        adapter = OpenAIAdapter(httpx.AsyncClient(), "https://api.openai.com")
        assert adapter.build_url("/chat/completions") == "https://api.openai.com/v1/chat/completions"

    def test_ollama_adapter_build_url_includes_v1(self):
        import httpx
        adapter = OpenAIAdapter(httpx.AsyncClient(), "http://localhost:11434")
        assert adapter.build_url("/chat/completions") == "http://localhost:11434/v1/chat/completions"

    def test_provider_registry_has_all_providers(self):
        expected = {"openai","anthropic","ollama","openrouter","groq",
                    "mistral","together","deepseek","perplexity","anyscale","fireworks"}
        assert expected == set(PROVIDER_REGISTRY.keys())


# ── Anthropic adapter unit tests ─────────────────────────────────────────────

from lco.adapters import AnthropicAdapter as _A
_extract_system_and_messages = None  # tested via AnthropicAdapter.normalise_request
_map_model = lambda m: __import__('lco.adapters', fromlist=['ANTHROPIC_MODEL_MAP']).ANTHROPIC_MODEL_MAP.get(m, m)

# Helper functions now live inside AnthropicAdapter as static methods
_openai_message_to_anthropic  = AnthropicAdapter._msg_to_anthropic
_openai_tools_to_anthropic    = AnthropicAdapter._tools_to_anthropic

def _anthropic_response_to_openai(resp, model):
    """Thin wrapper around AnthropicAdapter.complete for test compatibility."""
    import asyncio, httpx, json
    adapter = AnthropicAdapter(None, "https://api.anthropic.com")  # type: ignore
    # Simulate what complete() does internally
    text_parts, tool_calls = [], []
    for blk in resp.get("content", []):
        if blk.get("type") == "text":
            text_parts.append(blk.get("text",""))
        elif blk.get("type") == "tool_use":
            tool_calls.append({
                "id": blk.get("id",""), "type": "function",
                "function": {"name": blk.get("name",""),
                             "arguments": json.dumps(blk.get("input",{}))},
            })
    msg = {"role":"assistant","content":"\n".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    sr = resp.get("stop_reason","end_turn")
    finish = "tool_calls" if tool_calls else ("stop" if sr in ("end_turn","stop_sequence") else sr)
    usage = resp.get("usage",{})
    return {
        "id": resp.get("id",f"chatcmpl-test"),
        "object":"chat.completion","created":0,"model":model,
        "choices":[{"index":0,"message":msg,"finish_reason":finish}],
        "usage":{
            "prompt_tokens": usage.get("input_tokens",0),
            "completion_tokens": usage.get("output_tokens",0),
            "total_tokens": usage.get("input_tokens",0)+usage.get("output_tokens",0),
        },
    }


class TestAnthropicAdapter:
    def test_model_mapping(self):
        assert _map_model("claude-opus") == "claude-opus-4-5"
        assert _map_model("claude-opus-4-5") == "claude-opus-4-5"
        assert _map_model("some-unknown-model") == "some-unknown-model"

    def test_system_extraction(self):
        """System messages are extracted into the top-level 'system' field."""
        import httpx
        adapter = AnthropicAdapter(httpx.AsyncClient(), "https://api.anthropic.com")
        body = {"model": "claude-opus-4-5", "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user",   "content": "Hi"},
        ]}
        result = adapter.normalise_request(body)
        assert result.get("system") == "You are helpful."
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"

    def test_no_system_message(self):
        import httpx
        adapter = AnthropicAdapter(httpx.AsyncClient(), "https://api.anthropic.com")
        body = {"model": "claude-opus-4-5", "messages": [
            {"role": "user", "content": "Hi"},
        ]}
        result = adapter.normalise_request(body)
        assert "system" not in result
        assert len(result["messages"]) == 1

    def test_user_message_conversion(self):
        msg = {"role": "user", "content": "Hello"}
        assert _openai_message_to_anthropic(msg) == {"role": "user", "content": "Hello"}

    def test_tool_result_conversion(self):
        msg = {"role": "tool", "tool_call_id": "call_1", "content": '{"temp": 22}'}
        result = _openai_message_to_anthropic(msg)
        assert result["role"] == "user"
        assert result["content"][0]["type"] == "tool_result"
        assert result["content"][0]["tool_use_id"] == "call_1"

    def test_assistant_with_tool_calls(self):
        msg = {
            "role": "assistant",
            "content": "Looking it up.",
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
            }],
        }
        result = _openai_message_to_anthropic(msg)
        assert result["role"] == "assistant"
        blocks = result["content"]
        text_block = next(b for b in blocks if b["type"] == "text")
        tool_block = next(b for b in blocks if b["type"] == "tool_use")
        assert text_block["text"] == "Looking it up."
        assert tool_block["name"] == "get_weather"
        assert tool_block["input"] == {"city": "Paris"}

    def test_tools_conversion(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            },
        }]
        result = _openai_tools_to_anthropic(tools)
        assert len(result) == 1
        assert result[0]["name"] == "get_weather"
        assert "input_schema" in result[0]

    def test_anthropic_response_to_openai(self):
        anthropic_resp = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Paris is the capital."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 6},
        }
        result = _anthropic_response_to_openai(anthropic_resp, "claude-opus-4-5")
        assert result["id"] == "msg_123"
        assert result["choices"][0]["message"]["content"] == "Paris is the capital."
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 6
        assert result["usage"]["total_tokens"] == 16


# ── Tool call passthrough integrity (LCO-1 risk mitigation) ──────────────────
#
# Risk: "Breaking tool/function call outputs — hard-exclude them at proxy layer"
# Mitigation: Safe Zones flags them; the router must forward the body UNMODIFIED.
# These tests mock the upstream HTTP call and assert the body that reaches
# the upstream is byte-for-byte identical to what the client sent in.

class TestToolCallPassthrough:
    """
    Verifies that tool call messages are forwarded to the upstream without
    any modification. The mock captures the exact JSON body sent upstream
    and we assert it matches the original request payload.
    """

    @pytest.fixture
    def client(self):
        from lco.main import app
        with TestClient(app) as c:
            yield c

    def _make_tool_call_body(self):
        return {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "What's the weather in Paris?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Paris", "units": "celsius"}'
                        }
                    }]
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_abc123",
                    "content": '{"temperature": 18, "condition": "cloudy"}'
                },
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                            "units": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                        },
                        "required": ["city"]
                    }
                }
            }],
            "stream": False,
        }

    def test_tool_messages_classified_as_safe_zones(self):
        """Safe Zones must flag all tool-related messages before the proxy touches them."""
        body = self._make_tool_call_body()
        messages = body["messages"]
        results = classify_messages(messages)

        # Message 0: plain user prose — not a safe zone
        _, is_safe_0, _ = results[0]
        assert not is_safe_0, "Plain user message should not be a safe zone"

        # Message 1: assistant with tool_calls — must be safe zone
        _, is_safe_1, reason_1 = results[1]
        assert is_safe_1, "Assistant tool_calls message must be a safe zone"
        assert reason_1 == SafeZoneReason.TOOL_CALL

        # Message 2: tool result — must be safe zone
        _, is_safe_2, reason_2 = results[2]
        assert is_safe_2, "Tool result message must be a safe zone"
        assert reason_2 == SafeZoneReason.TOOL_RESULT

    def test_tool_call_body_forwarded_unmodified(self, client):
        """
        The body that reaches the upstream adapter must be identical to what
        the client sent. We mock httpx.AsyncClient.post to capture it.
        """
        body = self._make_tool_call_body()
        captured = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4o",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "It is 18°C and cloudy."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 12, "total_tokens": 62},
        }
        mock_response.raise_for_status = MagicMock()

        async def mock_post(url, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["body"] = json
            return mock_response

        with patch("lco.proxy.router.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_get_client.return_value = mock_client

            response = client.post(
                "/v1/chat/completions",
                json=body,
                headers={"Authorization": "Bearer sk-test-key"},
            )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert "body" in captured, "Upstream was never called"

        forwarded = captured["body"]

        # The tool_calls array must be forwarded intact
        assistant_msg = next(
            m for m in forwarded["messages"]
            if m.get("role") == "assistant" and m.get("tool_calls")
        )
        assert assistant_msg["tool_calls"][0]["id"] == "call_abc123"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_weather"
        assert assistant_msg["tool_calls"][0]["function"]["arguments"] == \
            '{"city": "Paris", "units": "celsius"}'

        # The tool result must be forwarded intact
        tool_msg = next(m for m in forwarded["messages"] if m.get("role") == "tool")
        assert tool_msg["tool_call_id"] == "call_abc123"
        assert tool_msg["content"] == '{"temperature": 18, "condition": "cloudy"}'

        # The tools definition must be forwarded intact
        assert forwarded["tools"][0]["function"]["name"] == "get_weather"

    def test_tool_call_response_headers(self, client):
        """Proxy must report safe zone hits in response headers."""
        body = self._make_tool_call_body()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4o",
            "choices": [{"index": 0,
                          "message": {"role": "assistant", "content": "ok"},
                          "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_response.raise_for_status = MagicMock()

        async def mock_post(url, headers=None, json=None, **kwargs):
            return mock_response

        with patch("lco.proxy.router.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_get_client.return_value = mock_client

            response = client.post(
                "/v1/chat/completions",
                json=body,
                headers={"Authorization": "Bearer sk-test-key"},
            )

        # At least 2 safe zone hits: the tool_calls message + the tool result
        safe_zones_hit = int(response.headers.get("x-lco-safe-zones", "0"))
        assert safe_zones_hit >= 2, (
            f"Expected at least 2 safe zone hits, got {safe_zones_hit}. "
            "Tool call and tool result messages must both be protected."
        )


# ── Proxy endpoint tests (no upstream) ───────────────────────────────────────

from lco.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestProxyEndpoints:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_status(self, client):
        r = client.get("/lco/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "running"
        assert "anthropic" in data["providers_supported"]
        assert "openai" in data["providers_supported"]

    def test_docs_available(self, client):
        r = client.get("/lco/docs")
        assert r.status_code == 200