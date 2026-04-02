"""
LCO — Unified Adapter Layer
============================
All upstream providers in one file.

Provider map
────────────
OpenAI-compatible (passthrough — no translation needed):
  openai        api.openai.com          gpt-4o, gpt-4o-mini, o1, ...
  ollama        localhost:11434          llama3.2, qwen2.5, mistral, ...
  openrouter    openrouter.ai            any model via unified API
  groq          api.groq.com             llama3, mixtral (fast inference)
  mistral       api.mistral.ai           mistral-large, codestral, ...
  together      api.together.xyz         llama, qwen, deepseek, ...
  deepseek      api.deepseek.com         deepseek-chat, deepseek-coder
  perplexity    api.perplexity.ai        llama-3.1-sonar, ...
  anyscale      api.endpoints.anyscale.com
  fireworks     api.fireworks.ai

Anthropic (native format — full translation required):
  anthropic     api.anthropic.com        claude-opus-4-5, claude-sonnet-4-6, ...

Detection priority
──────────────────
1. x-lco-provider header  (explicit override)
2. Model name prefix      (claude-* → anthropic)
3. API key format         (sk-ant-* → anthropic)
4. Base URL match         (openrouter.ai → openrouter, etc.)
5. Default               → openai
"""

from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import httpx


# ── Model aliases ─────────────────────────────────────────────────────────────

ANTHROPIC_MODEL_MAP: dict[str, str] = {
    "claude":           "anthropic/claude-opus-4.6",
    "claude-opus":      "anthropic/claude-opus-4.6",
    "claude-sonnet":    "anthropic/claude-sonnet-4.6",
    "claude-haiku":     "anthropic/claude-haiku-4.6",
    "claude-3-opus":    "anthropic/claude-opus-4.6",
    "claude-3-sonnet":  "anthropic/claude-sonnet-4.6",
    "claude-3-haiku":   "anthropic/claude-haiku-4.6",
}

ANTHROPIC_VERSION = "2023-06-01"

# Providers whose base URLs we can recognise automatically
_URL_PROVIDER_MAP = {
    "openrouter.ai":            "openrouter",
    "api.groq.com":             "groq",
    "api.mistral.ai":           "mistral",
    "api.together.xyz":         "together",
    "api.deepseek.com":         "deepseek",
    "api.perplexity.ai":        "perplexity",
    "api.endpoints.anyscale":   "anyscale",
    "api.fireworks.ai":         "fireworks",
    "localhost:11434":          "ollama",
    "127.0.0.1:11434":          "ollama",
    "api.anthropic.com":        "anthropic",
}


# ══════════════════════════════════════════════════════════════════════════════
# Base adapter
# ══════════════════════════════════════════════════════════════════════════════

class BaseAdapter(ABC):
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self.client   = client
        self.base_url = base_url.rstrip("/")

    @abstractmethod
    def build_headers(self, incoming: dict[str, str]) -> dict[str, str]: ...

    @abstractmethod
    def build_url(self, path: str) -> str: ...

    @abstractmethod
    def normalise_request(self, body: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def stream(self, url: str, headers: dict[str, str],
               body: dict[str, Any]) -> AsyncGenerator[bytes, None]: ...

    @abstractmethod
    async def complete(self, url: str, headers: dict[str, str],
                       body: dict[str, Any]) -> httpx.Response: ...

    @staticmethod
    def _clean(headers: dict[str, str]) -> dict[str, str]:
        return {k: v for k, v in headers.items()
                if not k.lower().startswith("x-lco-")}


# ══════════════════════════════════════════════════════════════════════════════
# OpenAI-compatible adapter  (OpenAI, Ollama, OpenRouter, Groq, Mistral, …)
# ══════════════════════════════════════════════════════════════════════════════

class OpenAIAdapter(BaseAdapter):
    """
    Passthrough adapter for any OpenAI-compatible endpoint.
    No translation needed — just forwards the request and response.
    """

    # Extra headers some providers require
    PROVIDER_HEADERS: dict[str, dict[str, str]] = {
        "openrouter": {
            "HTTP-Referer": "https://github.com/lco-proxy/lco",
            "X-Title":      "LCO Proxy",
        },
    }

    def __init__(self, client: httpx.AsyncClient, base_url: str,
                 provider: str = "openai") -> None:
        super().__init__(client, base_url)
        self.provider = provider

    def build_headers(self, incoming: dict[str, str]) -> dict[str, str]:
        clean = self._clean(dict(incoming))
        headers: dict[str, str] = {"Content-Type": "application/json"}

        import os as _os
        # Pick the right stored key for the upstream provider from the URL.
        # Priority: incoming auth header → URL-matched key → active key → OpenAI key
        url = self.base_url.lower()
        if "groq" in url:
            stored_key = _os.environ.get("LCO_KEY_GROQ", "")
        elif "openrouter" in url:
            stored_key = _os.environ.get("LCO_KEY_OPENROUTER", "")
        elif "mistral" in url:
            stored_key = _os.environ.get("LCO_KEY_MISTRAL", "")
        elif "together" in url:
            stored_key = _os.environ.get("LCO_KEY_OPENROUTER", "")
        elif "deepseek" in url or "fireworks" in url or "perplexity" in url:
            stored_key = _os.environ.get("LCO_KEY_OPENAI", "")
        else:
            stored_key = _os.environ.get("LCO_KEY_OPENAI", "")

        auth = (clean.get("authorization") or
                clean.get("Authorization") or
                clean.get("x-api-key") or
                stored_key or
                _os.environ.get("LCO_API_KEY") or
                _os.environ.get("OPENAI_API_KEY") or "")
        if auth:
            headers["Authorization"] = auth if auth.startswith("Bearer ") \
                                        else f"Bearer {auth}"

        # Forward org/project headers for OpenAI
        for k in ("openai-organization", "openai-project"):
            v = clean.get(k) or clean.get(k.title(), "")
            if v:
                headers[k] = v

        # Provider-specific extras
        headers.update(self.PROVIDER_HEADERS.get(self.provider, {}))
        return headers

    def build_url(self, path: str) -> str:
        return f"{self.base_url}/v1{path}"

    def normalise_request(self, body: dict[str, Any]) -> dict[str, Any]:
        return body   # no translation needed

    async def stream(self, url: str, headers: dict[str, str],
                     body: dict[str, Any]) -> AsyncGenerator[bytes, None]:
        async with self.client.stream("POST", url, headers=headers,
                                      json=body) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk

    async def complete(self, url: str, headers: dict[str, str],
                       body: dict[str, Any]) -> httpx.Response:
        resp = await self.client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp


# ══════════════════════════════════════════════════════════════════════════════
# Anthropic adapter  (full OpenAI ↔ Anthropic translation)
# ══════════════════════════════════════════════════════════════════════════════

class AnthropicAdapter(BaseAdapter):
    """
    Translates OpenAI chat completion format ↔ Anthropic Messages API.
    Handles: system extraction, tool calls, streaming re-emission.
    """

    def build_headers(self, incoming: dict[str, str]) -> dict[str, str]:
        clean = self._clean(dict(incoming))
        import os
        
        # Look for the Anthropic key injected by tray.py
        stored_key = os.environ.get("LCO_KEY_ANTHROPIC") or os.environ.get("LCO_API_KEY", "")
        
        api_key = (
            clean.get("x-api-key") or clean.get("X-Api-Key") or
            (clean.get("authorization", "") or
             clean.get("Authorization", "")).removeprefix("Bearer ").strip() or
            stored_key
        )
        return {
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def build_url(self, path: str) -> str:
        return f"{self.base_url}/v1/messages"

    def normalise_request(self, body: dict[str, Any]) -> dict[str, Any]:
        messages = body.get("messages",[])
        system_parts, user_msgs = [],[]
        for m in messages:
            if m.get("role") == "system":
                c = m.get("content", "")
                if isinstance(c, str):
                    system_parts.append(c)
                elif isinstance(c, list):
                    system_parts.extend(
                        b["text"] for b in c
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
            else:
                user_msgs.append(self._msg_to_anthropic(m))

        model_name = body.get("model", "")
        model = ANTHROPIC_MODEL_MAP.get(
            model_name, model_name or "anthropic/claude-sonnet-4.6"
        )
        
        payload: dict[str, Any] = {
            "model":      model,
            "max_tokens": body.get("max_tokens") or 4096,
            "messages":   user_msgs,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if body.get("temperature") is not None:
            payload["temperature"] = body["temperature"]
        if body.get("top_p") is not None:
            payload["top_p"] = body["top_p"]
        if body.get("stop"):
            payload["stop_sequences"] = (
                body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
            )
        if body.get("tools"):
            payload["tools"] = self._tools_to_anthropic(body["tools"])
        if body.get("stream"):
            payload["stream"] = True
        return payload

    @staticmethod
    def _msg_to_anthropic(msg: dict[str, Any]) -> dict[str, Any]:
        role, content = msg["role"], msg.get("content", "")
        if role == "tool":
            return {"role": "user", "content": [{
                "type":        "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content":     content if isinstance(content, str)
                               else json.dumps(content),
            }]}
        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in msg["tool_calls"]:
                fn   = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try: args = json.loads(args)
                    except json.JSONDecodeError: args = {}
                blocks.append({
                    "type":  "tool_use",
                    "id":    tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
                    "name":  fn.get("name", ""),
                    "input": args,
                })
            return {"role": "assistant", "content": blocks}
        if isinstance(content, list):
            return {"role": role, "content": content}
        return {"role": role, "content": content}

    @staticmethod
    def _tools_to_anthropic(tools: list[dict]) -> list[dict]:
        out = []
        for t in tools:
            if t.get("type") == "function":
                fn = t["function"]
                out.append({
                    "name":         fn["name"],
                    "description":  fn.get("description", ""),
                    "input_schema": fn.get("parameters",
                                          {"type": "object", "properties": {}}),
                })
        return out

    async def stream(self, url: str, headers: dict[str, str],
                     body: dict[str, Any]) -> AsyncGenerator[bytes, None]:
        cid   = f"chatcmpl-{uuid.uuid4().hex}"
        model = body.get("model", "claude-opus-4-5")
        ts    = int(time.time())

        def _chunk(delta: dict, finish: str | None = None) -> bytes:
            event = {
                "id": cid, "object": "chat.completion.chunk",
                "created": ts, "model": model,
                "choices": [{"index": 0, "delta": delta,
                             "finish_reason": finish}],
            }
            return f"data: {json.dumps(event)}\n\n".encode()

        yield _chunk({"role": "assistant", "content": ""})
        finish_reason = "stop"

        async with self.client.stream("POST", url, headers=headers,
                                      json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type", "")
                if t == "content_block_delta":
                    d = ev.get("delta", {})
                    if d.get("type") == "text_delta":
                        yield _chunk({"content": d.get("text", "")})
                elif t == "message_delta":
                    sr = ev.get("delta", {}).get("stop_reason", "end_turn")
                    finish_reason = "stop" if sr in ("end_turn", "stop_sequence") else sr
                elif t == "message_stop":
                    break

        yield _chunk({}, finish_reason)
        yield b"data: [DONE]\n\n"

    async def complete(self, url: str, headers: dict[str, str],
                       body: dict[str, Any]) -> httpx.Response:
        resp = await self.client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        a = resp.json()
        text_parts, tool_calls = [], []
        for blk in a.get("content", []):
            if blk.get("type") == "text":
                text_parts.append(blk.get("text", ""))
            elif blk.get("type") == "tool_use":
                tool_calls.append({
                    "id": blk.get("id", ""), "type": "function",
                    "function": {
                        "name":      blk.get("name", ""),
                        "arguments": json.dumps(blk.get("input", {})),
                    },
                })
        msg: dict[str, Any] = {
            "role":    "assistant",
            "content": "\n".join(text_parts) or None,
        }
        if tool_calls:
            msg["tool_calls"] = tool_calls
        sr = a.get("stop_reason", "end_turn")
        finish = "tool_calls" if tool_calls else (
            "stop" if sr in ("end_turn", "stop_sequence") else sr
        )
        usage = a.get("usage", {})
        oai = {
            "id":      a.get("id", f"chatcmpl-{uuid.uuid4().hex}"),
            "object":  "chat.completion",
            "created": int(time.time()),
            "model":   body.get("model", ""),
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage":   {
                "prompt_tokens":     usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens":      usage.get("input_tokens", 0) +
                                     usage.get("output_tokens", 0),
            },
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(oai).encode(),
        )


# ══════════════════════════════════════════════════════════════════════════════
# Provider registry & factory
# ══════════════════════════════════════════════════════════════════════════════

# All known providers with their default base URLs
# OpenAI-compatible ones share the OpenAIAdapter; Anthropic has its own.
PROVIDER_REGISTRY: dict[str, dict[str, Any]] = {
    "openai":      {"url": "https://api.openai.com",              "adapter": "openai"},
    "anthropic":   {"url": "https://api.anthropic.com",           "adapter": "anthropic"},
    "ollama":      {"url": "http://localhost:11434",              "adapter": "openai"},
    "openrouter":  {"url": "https://openrouter.ai/api",           "adapter": "openai"},
    "groq":        {"url": "https://api.groq.com/openai",         "adapter": "openai"},
    "mistral":     {"url": "https://api.mistral.ai",              "adapter": "openai"},
    "together":    {"url": "https://api.together.xyz",            "adapter": "openai"},
    "deepseek":    {"url": "https://api.deepseek.com",            "adapter": "openai"},
    "perplexity":  {"url": "https://api.perplexity.ai",           "adapter": "openai"},
    "anyscale":    {"url": "https://api.endpoints.anyscale.com",  "adapter": "openai"},
    "fireworks":   {"url": "https://api.fireworks.ai/inference",  "adapter": "openai"},
}


def _detect_provider(headers: dict[str, str], body: dict[str, Any],
                     openai_base_url: str = "") -> str:
    """Detect provider from headers, model name, key format, or base URL."""
    # 1. Explicit header wins
    x = headers.get("x-lco-provider", "").lower()
    if x in PROVIDER_REGISTRY:
        return x

    # 2. Model name prefix
    model = body.get("model", "")
    if model.startswith("claude"):
        return "anthropic"

    # 3. API key format
    auth = (headers.get("authorization", "") or
            headers.get("x-api-key", ""))
    if "sk-ant" in auth:
        return "anthropic"

    # 4. Base URL pattern
    for fragment, provider in _URL_PROVIDER_MAP.items():
        if fragment in openai_base_url:
            return provider

    return "openai"


def get_adapter(headers: dict[str, str], body: dict[str, Any],
                client: httpx.AsyncClient,
                openai_base_url: str = "https://api.openai.com",
                anthropic_base_url: str = "https://api.anthropic.com") -> BaseAdapter:
    """Return the correct adapter for this request."""
    import os
    
    # NEW: Read live overrides from headers (for testing) or environment (for saved state)
    live_openai = headers.get("x-lco-base-url") or os.environ.get("LCO_OPENAI_BASE_URL") or openai_base_url
    live_anthropic = os.environ.get("LCO_ANTHROPIC_BASE_URL") or anthropic_base_url

    provider = _detect_provider(headers, body, live_openai)
    
    if provider == "anthropic":
        return AnthropicAdapter(client, live_anthropic)

    return OpenAIAdapter(client, live_openai, provider=provider)