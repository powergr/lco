"""
LCO-4 — Basic Quality Gate
============================
Checks that a compressed text retains enough semantic similarity to the
original before allowing the compressed version to be delivered to the client.
If the similarity score falls below the configured threshold, the original
text is delivered unchanged (passthrough) and a warning is logged.

Architecture
────────────
The gate is pluggable: a QualityGate instance holds an EmbedFn that converts
text → a float vector. Three backends are provided:

  tfidf_embedder     – pure Python + numpy, no external services required.
                       Fast, deterministic, good for MVP and unit tests.
                       Ships as the default.

  ollama_embedder    – calls Ollama's /api/embed endpoint to get real
                       neural embeddings from a locally-running model.
                       Better semantic coverage; requires Ollama running.

  null_embedder      – always returns score=1.0, used to disable the gate
                       without changing config (e.g. in performance tests).

The gate is stateless per-request. It is called from the router after the
cleaner (LCO-3) runs on input messages, and from the buffer after output
compression runs (LCO-2/LCO-6).

Configuration (from .env)
─────────────────────────
LCO_QUALITY_GATE=true          enable / disable
LCO_QUALITY_THRESHOLD=0.85     minimum acceptable similarity (0.0 – 1.0)
LCO_EMBEDDER=tfidf             tfidf | ollama | null
LCO_OLLAMA_EMBED_MODEL=nomic-embed-text   model used for Ollama embeddings
LCO_OLLAMA_BASE_URL=http://localhost:11434  where Ollama is running
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Awaitable

import httpx

logger = logging.getLogger("lco.quality_gate")

# ── Types ─────────────────────────────────────────────────────────────────────

Vector = list[float]
EmbedFn = Callable[[str], Awaitable[Vector]]


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class GateResult:
    passed: bool
    score: float               # cosine similarity, 0.0 – 1.0
    threshold: float
    embedder: str
    original_chars: int
    compressed_chars: int

    @property
    def char_reduction_pct(self) -> float:
        if self.original_chars == 0:
            return 0.0
        return 100.0 * (self.original_chars - self.compressed_chars) / self.original_chars


# ── Cosine similarity ─────────────────────────────────────────────────────────

def _cosine(a: Vector, b: Vector) -> float:
    """Pure-Python cosine similarity. Returns 1.0 for identical zero vectors."""
    if not a or not b or len(a) != len(b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 1.0
    return max(0.0, min(1.0, dot / (mag_a * mag_b)))


# ── TF-IDF embedder ───────────────────────────────────────────────────────────

_TOKENISE = re.compile(r"\b\w+\b")


def _tokenise(text: str) -> list[str]:
    return _TOKENISE.findall(text.lower())


def _tfidf_vector(text: str, vocab: dict[str, int]) -> Vector:
    """
    Build a TF vector over a shared vocabulary.
    Simple term frequency; IDF is implicit through the shared vocab approach
    (rare terms get their own dimension, common terms are naturally downweighted
    by being spread across many dimensions relative to the document length).
    """
    tokens = _tokenise(text)
    if not tokens:
        return [0.0] * len(vocab)
    counts = Counter(tokens)
    total = len(tokens)
    vec = [0.0] * len(vocab)
    for term, idx in vocab.items():
        vec[idx] = counts.get(term, 0) / total
    return vec


def _build_vocab(*texts: str) -> dict[str, int]:
    """Build a shared vocabulary from all provided texts."""
    all_tokens: set[str] = set()
    for t in texts:
        all_tokens.update(_tokenise(t))
    return {term: i for i, term in enumerate(sorted(all_tokens))}


async def tfidf_embedder(text: str) -> Vector:
    """
    Stateless TF-IDF embedder. Returns the raw TF vector for a single text.
    Similarity must be computed over a shared vocabulary — see QualityGate.check().
    """
    # Single-document: return bag-of-words unit vector
    tokens = _tokenise(text)
    if not tokens:
        return []
    counts = Counter(tokens)
    total = len(tokens)
    # Return list of (term, tf) pairs encoded as a flat vector by sorted term index
    # For single-text use the vector is just the TF distribution
    vocab = {term: i for i, term in enumerate(sorted(counts.keys()))}
    vec = [0.0] * len(vocab)
    for term, idx in vocab.items():
        vec[idx] = counts[term] / total
    return vec


# ── Ollama embedder ───────────────────────────────────────────────────────────

class OllamaEmbedder:
    """
    Calls Ollama's /api/embed endpoint to get neural embeddings.
    Requires Ollama running locally with an embedding-capable model.

    Recommended models (pull with `ollama pull <model>`):
      nomic-embed-text   – 768-dim, fast, good quality  (~274 MB)
      mxbai-embed-large  – 1024-dim, higher quality     (~670 MB)
      all-minilm         – 384-dim, very fast            (~46 MB)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        timeout: float = 60.0,  # first call loads model into RAM — needs room
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=5.0)
        )
        return self._client

    async def embed(self, text: str) -> Vector:
        """Return embedding vector from Ollama. Raises on connection failure."""
        client = self._get_client()
        resp = await client.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama /api/embed returns {"embeddings": [[...float...]]}
        embeddings = data.get("embeddings") or data.get("embedding")
        if isinstance(embeddings, list) and embeddings:
            first = embeddings[0]
            if isinstance(first, list):
                return [float(x) for x in first]
            return [float(x) for x in embeddings]
        raise ValueError(f"Unexpected Ollama embed response: {data}")

    async def __call__(self, text: str) -> Vector:
        return await self.embed(text)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()


# ── Null embedder (disables gate) ─────────────────────────────────────────────

async def null_embedder(text: str) -> Vector:
    """Always returns a unit vector. Gate always passes. Use to disable."""
    return [1.0]


# ── Quality Gate ──────────────────────────────────────────────────────────────

class QualityGate:
    """
    Compares original and compressed text using cosine similarity over
    shared-vocabulary TF-IDF vectors (default) or a pluggable embedder.

    Usage
    ─────
        gate = QualityGate(threshold=0.85)
        result = await gate.check(original_text, compressed_text)
        if not result.passed:
            # fall back to original
            text_to_deliver = original_text
        else:
            text_to_deliver = compressed_text
    """

    def __init__(
        self,
        threshold: float = 0.85,
        embedder: str = "tfidf",
        ollama_base_url: str = "http://localhost:11434",
        ollama_model: str = "nomic-embed-text",
        ollama_timeout: float = 60.0,
        enabled: bool = True,
    ) -> None:
        self.threshold = threshold
        self.embedder_name = embedder
        self.enabled = enabled
        self._ollama: OllamaEmbedder | None = None

        if embedder == "ollama":
            self._ollama = OllamaEmbedder(
                base_url=ollama_base_url,
                model=ollama_model,
                timeout=ollama_timeout,
            )

    async def check(
        self,
        original: str,
        compressed: str,
    ) -> GateResult:
        """
        Compute similarity and return a GateResult.
        Never raises — on any error, passes with score=1.0 so we don't
        accidentally block content due to an infrastructure issue.
        """
        if not self.enabled:
            return GateResult(
                passed=True, score=1.0, threshold=self.threshold,
                embedder="disabled",
                original_chars=len(original),
                compressed_chars=len(compressed),
            )

        # Identical text always passes
        if original == compressed:
            return GateResult(
                passed=True, score=1.0, threshold=self.threshold,
                embedder=self.embedder_name,
                original_chars=len(original),
                compressed_chars=len(compressed),
            )

        # Empty compressed → reject
        if not compressed.strip():
            return GateResult(
                passed=False, score=0.0, threshold=self.threshold,
                embedder=self.embedder_name,
                original_chars=len(original),
                compressed_chars=len(compressed),
            )

        try:
            score = await self._compute_similarity(original, compressed)
        except Exception as exc:
            logger.warning(
                "Quality gate error (%s): %s — passing through",
                self.embedder_name, exc,
            )
            return GateResult(
                passed=True, score=1.0, threshold=self.threshold,
                embedder=self.embedder_name,
                original_chars=len(original),
                compressed_chars=len(compressed),
            )

        passed = score >= self.threshold
        if not passed:
            logger.warning(
                "Quality gate FAIL: score=%.3f threshold=%.3f  "
                "original=%d chars  compressed=%d chars — reverting to original",
                score, self.threshold, len(original), len(compressed),
            )
        else:
            logger.debug(
                "Quality gate OK: score=%.3f threshold=%.3f  reduction=%.1f%%",
                score, self.threshold,
                100 * (len(original) - len(compressed)) / max(1, len(original)),
            )

        return GateResult(
            passed=passed, score=score, threshold=self.threshold,
            embedder=self.embedder_name,
            original_chars=len(original),
            compressed_chars=len(compressed),
        )

    async def _compute_similarity(self, a: str, b: str) -> float:
        if self.embedder_name == "tfidf":
            return self._tfidf_similarity(a, b)
        elif self.embedder_name == "ollama" and self._ollama:
            vec_a = await self._ollama.embed(a)
            vec_b = await self._ollama.embed(b)
            return _cosine(vec_a, vec_b)
        elif self.embedder_name == "null":
            return 1.0
        else:
            return self._tfidf_similarity(a, b)

    @staticmethod
    def _tfidf_similarity(a: str, b: str) -> float:
        """
        Compute cosine similarity over a shared TF-IDF vocabulary.
        Both documents are vectorised over the union of their terms.
        """
        vocab = _build_vocab(a, b)
        vec_a = _tfidf_vector(a, vocab)
        vec_b = _tfidf_vector(b, vocab)
        return _cosine(vec_a, vec_b)

    async def close(self) -> None:
        if self._ollama:
            await self._ollama.close()


# ── Singleton factory ─────────────────────────────────────────────────────────

_gate: QualityGate | None = None


def get_quality_gate() -> QualityGate:
    """Return the singleton gate. Initialised from settings on first call."""
    global _gate
    if _gate is None:
        from ..config import settings
        _gate = QualityGate(
            threshold=settings.quality_threshold,
            embedder=getattr(settings, "embedder", "tfidf"),
            ollama_base_url=getattr(settings, "ollama_base_url",
                                    "http://localhost:11434"),
            ollama_model=getattr(settings, "ollama_embed_model",
                                 "nomic-embed-text"),
            ollama_timeout=getattr(settings, "ollama_embed_timeout", 60.0),
            enabled=settings.quality_gate_enabled,
        )
    return _gate


def reset_quality_gate() -> None:
    """Reset singleton — used in tests to re-initialise with different settings."""
    global _gate
    _gate = None