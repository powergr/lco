"""
LCO — LLM-Assisted Compressor
================================
Uses a locally-running Ollama model to intelligently summarise long messages.
This is qualitatively better than extractive TF-IDF compression because the
model understands meaning, not just word frequency.

When to use
───────────
- Messages longer than LCO_LLM_COMPRESS_MIN_TOKENS (default: 200)
- Only in medium/aggressive mode
- Only when Ollama is reachable (falls back to extractive compressor if not)
- Never on code blocks, JSON, tool calls (Safe Zones already handled upstream)

How it works
────────────
1. Send the message to Ollama with a tight compression prompt
2. Request a summary in ≤ N% of original length
3. Run the quality gate on the result
4. If gate fails or Ollama unavailable → fall back to extractive compressor

The compression prompt is tuned to preserve:
- Technical facts, numbers, code snippets
- The core answer/claim
- Proper nouns and identifiers

And remove:
- Filler phrases and pleasantries
- Repeated explanations
- Hedging language
"""

from __future__ import annotations
import logging
from typing import Optional

import httpx

logger = logging.getLogger("lco.llm_compress")

# ── Compression prompts by mode ───────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a precise text compressor. Summarise the given text, "
    "preserving all technical facts, numbers, code, and key conclusions. "
    "Remove pleasantries, filler phrases, repeated content, and hedging language. "
    "Return ONLY the compressed text with no preamble or explanation."
)

_USER_TEMPLATE = {
    "medium": (
        "Compress the following to roughly 55% of its length. "
        "Keep all technical facts and key information.\n\n"
        "TEXT:\n{text}\n\nCOMPRESSED:"
    ),
    "aggressive": (
        "Compress the following to roughly 35% of its length. "
        "Keep only the most essential technical information.\n\n"
        "TEXT:\n{text}\n\nCOMPRESSED:"
    ),
}


class LLMCompressor:
    """
    Compresses text using a local Ollama model.
    Falls back gracefully if Ollama is unreachable.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout: float = 30.0,
        min_tokens: int = 200,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.min_tokens = min_tokens      # only compress messages longer than this
        self._client: Optional[httpx.AsyncClient] = None
        self._available: Optional[bool] = None   # cached availability

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def is_available(self) -> bool:
        """Check once whether Ollama is reachable."""
        if self._available is not None:
            return self._available
        try:
            r = await self._get_client().get(
                f"{self.base_url}/api/tags", timeout=2.0
            )
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        return self._available

    async def compress(self, text: str, mode: str = "medium") -> str:
        """
        Compress text using the LLM. Returns original on any failure.
        Only compresses if text is longer than min_tokens.
        """
        # Length gate — don't spend LLM tokens on short messages
        if len(text) // 4 < self.min_tokens:
            return text

        if not await self.is_available():
            logger.debug("LLM compressor: Ollama not available, skipping")
            return text

        template = _USER_TEMPLATE.get(mode, _USER_TEMPLATE["medium"])
        user_msg = template.format(text=text)

        try:
            resp = await self._get_client().post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0,       # deterministic
                        "num_predict": len(text) // 2,  # cap output length
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            compressed = data.get("message", {}).get("content", "").strip()

            if not compressed:
                return text

            # Sanity check: compressed must be shorter and non-empty
            if len(compressed) >= len(text) * 0.95:
                logger.debug("LLM compressor: no meaningful reduction, keeping original")
                return text

            reduction_pct = 100 * (len(text) - len(compressed)) / len(text)
            logger.debug(
                "LLM compressed: %d → %d chars (%.1f%% reduction)",
                len(text), len(compressed), reduction_pct,
            )
            return compressed

        except Exception as exc:
            logger.debug("LLM compressor error: %s — falling back", exc)
            return text

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()


# ── Singleton ─────────────────────────────────────────────────────────────────

_llm_compressor: Optional[LLMCompressor] = None


def get_llm_compressor() -> LLMCompressor:
    global _llm_compressor
    if _llm_compressor is None:
        from ..config import settings
        _llm_compressor = LLMCompressor(
            base_url=getattr(settings, "ollama_base_url", "http://localhost:11434"),
            model=getattr(settings, "ollama_compress_model",
                          getattr(settings, "ollama_chat_model", "qwen2.5:7b")),
            timeout=getattr(settings, "ollama_compress_timeout", 30.0),
            min_tokens=getattr(settings, "llm_compress_min_tokens", 200),
        )
    return _llm_compressor


def reset_llm_compressor() -> None:
    global _llm_compressor
    _llm_compressor = None