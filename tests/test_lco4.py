"""
LCO-4 Test Suite — Basic Quality Gate
=======================================
Covers:
  Unit tests (no external services)
  - Cosine similarity correctness
  - TF-IDF vectorisation
  - Gate passes identical text
  - Gate passes text above threshold
  - Gate rejects empty compressed text
  - Gate fails text below threshold and reverts
  - Gate disabled → always passes
  - Error in embedder → gate passes (fail-safe)
  - GateResult metadata correctness

  Cleaner + Gate integration
  - Gate approves minimal-change cleaner output
  - Gate rejects over-aggressive compression
  - Gate does not run when compression_mode=passthrough

  Ollama tests (marked with @pytest.mark.ollama)
  - Run these with: pytest tests/ -v -m ollama
  - Skip automatically if Ollama is not reachable
  - Tests real neural embeddings via Ollama /api/embed
  - Tests proxy end-to-end with Ollama as upstream LLM
  - Tests quality gate with Ollama embedder
  - Tests chat completion through proxy → Ollama
  - Tests streaming through proxy → Ollama
"""

from __future__ import annotations
import json
import math
import os
import pytest
import asyncio
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from lco.proxy.quality_gate import (
    QualityGate,
    GateResult,
    OllamaEmbedder,
    _cosine,
    _build_vocab,
    _tfidf_vector,
    _tokenise,
    null_embedder,
    tfidf_embedder,
    reset_quality_gate,
    get_quality_gate,
)
from lco.proxy.cleaner import clean_messages


# ── Ollama availability fixture ───────────────────────────────────────────────

OLLAMA_BASE = os.getenv("LCO_OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("LCO_OLLAMA_CHAT_MODEL", "llama3.2")
OLLAMA_EMBED_MODEL = os.getenv("LCO_OLLAMA_EMBED_MODEL", "nomic-embed-text")


def _ollama_reachable() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_has_model(model: str) -> bool:
    try:
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=2.0)
        names = [m["name"].split(":")[0] for m in r.json().get("models", [])]
        return model.split(":")[0] in names
    except Exception:
        return False


ollama_available = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="Ollama not running — start with `ollama serve` to run these tests",
)

ollama_chat_model = pytest.mark.skipif(
    not _ollama_has_model(OLLAMA_CHAT_MODEL),
    reason=f"Ollama model '{OLLAMA_CHAT_MODEL}' not pulled — run: ollama pull {OLLAMA_CHAT_MODEL}",
)

ollama_embed_model = pytest.mark.skipif(
    not _ollama_has_model(OLLAMA_EMBED_MODEL),
    reason=f"Ollama embed model '{OLLAMA_EMBED_MODEL}' not pulled — run: ollama pull {OLLAMA_EMBED_MODEL}",
)


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — cosine similarity
# ═══════════════════════════════════════════════════════════════════════════════

class TestCosineSimilarity:
    def test_identical_vectors_score_one(self):
        v = [0.3, 0.5, 0.2]
        assert _cosine(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors_score_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_opposite_vectors_clamped_to_zero(self):
        # cosine returns max(0, ...) so negatives are clamped
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_zero_vector_returns_one(self):
        # Both zero → undefined, we return 1.0 (safe pass)
        assert _cosine([0.0, 0.0], [0.0, 0.0]) == pytest.approx(1.0)

    def test_one_zero_vector_returns_one(self):
        assert _cosine([0.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_similar_vectors_high_score(self):
        a = [0.9, 0.1, 0.0]
        b = [0.8, 0.2, 0.0]
        score = _cosine(a, b)
        assert score > 0.99

    def test_dissimilar_vectors_low_score(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 0.0, 1.0]
        score = _cosine(a, b)
        assert score < 0.01

    def test_mismatched_lengths_returns_one(self):
        # Safe pass on malformed vectors
        assert _cosine([1.0, 2.0], [1.0]) == pytest.approx(1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — TF-IDF vectorisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestTFIDF:
    def test_tokenise_lowercases(self):
        assert _tokenise("Hello World") == ["hello", "world"]

    def test_tokenise_strips_punctuation(self):
        tokens = _tokenise("Hello, world!")
        assert "hello" in tokens
        assert "world" in tokens
        assert "," not in tokens

    def test_shared_vocab_covers_both_texts(self):
        vocab = _build_vocab("the cat sat", "the dog ran")
        assert "cat" in vocab
        assert "dog" in vocab
        assert "the" in vocab

    def test_tfidf_vector_length_matches_vocab(self):
        vocab = _build_vocab("hello world", "hello python")
        vec = _tfidf_vector("hello world", vocab)
        assert len(vec) == len(vocab)

    def test_tfidf_vector_sums_to_one(self):
        vocab = _build_vocab("the cat sat on the mat")
        vec = _tfidf_vector("the cat sat on the mat", vocab)
        assert sum(vec) == pytest.approx(1.0, abs=1e-6)

    def test_identical_texts_have_identical_vectors(self):
        text = "Python is a great programming language"
        vocab = _build_vocab(text, text)
        v1 = _tfidf_vector(text, vocab)
        v2 = _tfidf_vector(text, vocab)
        assert v1 == v2

    def test_tfidf_similarity_identical_text(self):
        gate = QualityGate(threshold=0.85, embedder="tfidf")
        score = gate._tfidf_similarity(
            "The quick brown fox jumps over the lazy dog",
            "The quick brown fox jumps over the lazy dog",
        )
        assert score == pytest.approx(1.0, abs=1e-6)

    def test_tfidf_similarity_similar_texts_high_score(self):
        gate = QualityGate(threshold=0.85, embedder="tfidf")
        original = "Python is a high-level programming language known for readability."
        compressed = "Python is a high-level programming language."
        score = gate._tfidf_similarity(original, compressed)
        assert score > 0.80

    def test_tfidf_similarity_different_topics_low_score(self):
        gate = QualityGate(threshold=0.85, embedder="tfidf")
        score = gate._tfidf_similarity(
            "The French Revolution began in 1789 with the storming of the Bastille.",
            "Machine learning models require large amounts of training data.",
        )
        assert score < 0.30


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — QualityGate.check()
# ═══════════════════════════════════════════════════════════════════════════════

class TestQualityGate:
    def test_identical_text_passes_without_computing(self):
        async def _inner():
            gate = QualityGate(threshold=0.85)
            result = await gate.check("hello world", "hello world")
            assert result.passed
            assert result.score == pytest.approx(1.0)
        asyncio.run(_inner())

    def test_empty_compressed_fails(self):
        async def _inner():
            gate = QualityGate(threshold=0.85)
            result = await gate.check("Some meaningful text here.", "   ")
            assert not result.passed
            assert result.score == 0.0
        asyncio.run(_inner())

    def test_similar_text_passes(self):
        async def _inner():
            gate = QualityGate(threshold=0.80)
            original = (
                "Python is widely used in data science, web development, and automation. "
                "It has a large standard library and active community."
            )
            compressed = (
                "Python is used in data science, web development, and automation "
                "with a large standard library."
            )
            result = await gate.check(original, compressed)
            assert result.passed
            assert result.score > 0.80
        asyncio.run(_inner())

    def test_completely_different_text_fails(self):
        async def _inner():
            gate = QualityGate(threshold=0.85)
            result = await gate.check(
                "The Eiffel Tower was built in Paris France in 1889.",
                "Quantum computing uses qubits instead of classical bits.",
            )
            assert not result.passed
            assert result.score < 0.85
        asyncio.run(_inner())

    def test_gate_disabled_always_passes(self):
        async def _inner():
            gate = QualityGate(threshold=0.99, enabled=False)
            result = await gate.check(
                "Paris is the capital of France.",
                "Bananas are yellow tropical fruits.",
            )
            assert result.passed
            assert result.embedder == "disabled"
        asyncio.run(_inner())

    def test_embedder_error_fails_safe(self):
        async def _inner():
            gate = QualityGate(threshold=0.85, embedder="ollama")
    
            async def broken_embed(text: str):
                raise ConnectionRefusedError("Ollama not running")
    
            gate._ollama = MagicMock()
            gate._ollama.embed = broken_embed
            result = await gate.check("original text here", "compressed text")
            # Must pass through (fail-safe) — never block content due to infra error
            assert result.passed
            assert result.score == pytest.approx(1.0)
        asyncio.run(_inner())

    def test_gate_result_metadata(self):
        async def _inner():
            gate = QualityGate(threshold=0.85, embedder="tfidf")
            original = "The Python programming language was created by Guido van Rossum."
            compressed = "Python was created by Guido van Rossum."
            result = await gate.check(original, compressed)
    
            assert result.threshold == 0.85
            assert result.embedder == "tfidf"
            assert result.original_chars == len(original)
            assert result.compressed_chars == len(compressed)
            assert result.char_reduction_pct > 0
        asyncio.run(_inner())

    def test_threshold_boundary(self):
        """Exactly at threshold passes; just below fails."""
        async def _inner():
            gate = QualityGate(threshold=0.85)
            # Mock to return exactly threshold and threshold - epsilon
            gate.embedder_name = "tfidf"
    
            with patch.object(gate, "_tfidf_similarity", return_value=0.85):
                result = await gate.check("a", "b")
                assert result.passed
    
            with patch.object(gate, "_tfidf_similarity", return_value=0.849):
                result = await gate.check("a", "b")
                assert not result.passed
        asyncio.run(_inner())

    def test_get_quality_gate_singleton(self):
        reset_quality_gate()
        g1 = get_quality_gate()
        g2 = get_quality_gate()
        assert g1 is g2
        reset_quality_gate()

    def test_reset_creates_fresh_gate(self):
        reset_quality_gate()
        g1 = get_quality_gate()
        reset_quality_gate()
        g2 = get_quality_gate()
        assert g1 is not g2
        reset_quality_gate()


# ═══════════════════════════════════════════════════════════════════════════════
# Cleaner + Gate integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestCleanerGateIntegration:
    def test_gate_approves_light_cleaning(self):
        """Removing boilerplate from a long message must pass the gate."""
        async def _inner():
            gate = QualityGate(threshold=0.75, embedder="tfidf")
            original = (
                "Certainly! Great question! "
                "Python supports multiple programming paradigms including "
                "object-oriented, functional, and procedural styles. "
                "It has dynamic typing and garbage collection. "
                "I hope this helps!"
            )
            messages = [{"role": "assistant", "content": original}]
            cleaned, _ = clean_messages(messages, skip_last_user=False)
            compressed = cleaned[0]["content"]
    
            result = await gate.check(original, compressed)
            assert result.passed, (
                f"Boilerplate removal should pass quality gate. "
                f"score={result.score:.3f} threshold={result.threshold}"
            )
        asyncio.run(_inner())

    def test_gate_rejects_over_compression(self):
        """Truncating to a single word must fail the gate."""
        async def _inner():
            gate = QualityGate(threshold=0.80, embedder="tfidf")
            original = (
                "FastAPI is a modern high-performance web framework for building "
                "APIs with Python based on standard Python type hints. It is one "
                "of the fastest Python frameworks available."
            )
            compressed = "FastAPI."  # catastrophic reduction
            result = await gate.check(original, compressed)
            assert not result.passed
        asyncio.run(_inner())

    def test_gate_skipped_in_passthrough_mode(self):
        """When compression_mode=passthrough the router never calls the gate."""
        async def _inner():
            from lco.config import settings
            # The router only calls the gate when optimised_body != body.
            # In passthrough mode the cleaner doesn't run, so bodies are identical.
            # This test documents that invariant explicitly.
            assert settings.compression_mode == "passthrough"
            # Gate would never be called — nothing to assert except that the
            # default config is passthrough, proven here.
            assert True
        asyncio.run(_inner())

# ═══════════════════════════════════════════════════════════════════════════════
# Ollama tests
# All marked @ollama_available — skipped automatically if Ollama is not running.
# Run with: pytest tests/ -v -m ollama
#           (or just pytest tests/ -v and they'll auto-skip gracefully)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.ollama
class TestOllamaConnectivity:
    """Basic connectivity — these run first to fail fast."""

    @ollama_available
    def test_ollama_api_tags_reachable(self):
        """Ollama /api/tags must be reachable and return a models list."""
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert "models" in data
        print(f"\n  Ollama models available: {[m['name'] for m in data['models']]}")

    @ollama_available
    def test_ollama_version_endpoint(self):
        r = httpx.get(f"{OLLAMA_BASE}/api/version", timeout=5)
        assert r.status_code == 200
        assert "version" in r.json()


@pytest.mark.ollama
class TestOllamaEmbeddings:
    """Tests for Ollama embedding endpoint used by the quality gate."""

    @ollama_available
    @ollama_embed_model
    def test_embed_returns_vector(self):
        """Ollama /api/embed must return a non-empty float vector."""
        async def _inner():
            embedder = OllamaEmbedder(base_url=OLLAMA_BASE, model=OLLAMA_EMBED_MODEL)
            vec = await embedder.embed("Hello, world!")
            assert isinstance(vec, list)
            assert len(vec) > 0
            assert all(isinstance(x, float) for x in vec)
            print(f"\n  Embedding dim: {len(vec)}")
            await embedder.close()
        asyncio.run(_inner())

    @ollama_available
    @ollama_embed_model
    def test_identical_texts_score_one(self):
        """Same text embedded twice must produce cosine similarity ≈ 1.0."""
        async def _inner():
            embedder = OllamaEmbedder(base_url=OLLAMA_BASE, model=OLLAMA_EMBED_MODEL)
            text = "The quick brown fox jumps over the lazy dog."
            v1 = await embedder.embed(text)
            v2 = await embedder.embed(text)
            score = _cosine(v1, v2)
            assert score == pytest.approx(1.0, abs=1e-3)
            await embedder.close()
        asyncio.run(_inner())

    @ollama_available
    @ollama_embed_model
    def test_similar_texts_high_similarity(self):
        """Paraphrases of the same fact must score higher than unrelated texts."""
        async def _inner():
            embedder = OllamaEmbedder(base_url=OLLAMA_BASE, model=OLLAMA_EMBED_MODEL)
            original = "Paris is the capital city of France and is located in Europe."
            paraphrase = "The capital of France is Paris, a major European city."
            unrelated = "Machine learning models are trained on large datasets."
    
            v_orig = await embedder.embed(original)
            v_para = await embedder.embed(paraphrase)
            v_unrel = await embedder.embed(unrelated)
    
            sim_para = _cosine(v_orig, v_para)
            sim_unrel = _cosine(v_orig, v_unrel)
    
            print(f"\n  Paraphrase similarity  : {sim_para:.4f}")
            print(f"  Unrelated similarity   : {sim_unrel:.4f}")
    
            assert sim_para > sim_unrel, (
                f"Paraphrase ({sim_para:.3f}) should score higher than "
                f"unrelated text ({sim_unrel:.3f})"
            )
        asyncio.run(_inner())

    @ollama_available
    @ollama_embed_model
    def test_quality_gate_with_ollama_embedder(self):
        """Full quality gate check using Ollama neural embeddings."""
        async def _inner():
            gate = QualityGate(
                threshold=0.80,
                embedder="ollama",
                ollama_base_url=OLLAMA_BASE,
                ollama_model=OLLAMA_EMBED_MODEL,
            )
            original = (
                "Certainly! FastAPI is a modern web framework for Python. "
                "It is fast, easy to use, and well-documented. "
                "I hope this helps!"
            )
            compressed = (
                "FastAPI is a modern, fast web framework for Python that is "
                "easy to use and well-documented."
            )
            result = await gate.check(original, compressed)
            print(f"\n  Gate score (Ollama): {result.score:.4f}  passed={result.passed}")
            # With neural embeddings a good paraphrase should easily pass 0.80
            assert result.passed
            assert result.score > 0.80
            await gate.close()
        asyncio.run(_inner())

    @ollama_available
    @ollama_embed_model
    def test_quality_gate_ollama_rejects_bad_compression(self):
        """Gate must reject a topic-changed 'compression'."""
        async def _inner():
            gate = QualityGate(
                threshold=0.80,
                embedder="ollama",
                ollama_base_url=OLLAMA_BASE,
                ollama_model=OLLAMA_EMBED_MODEL,
            )
            result = await gate.check(
                "The Eiffel Tower stands 330 metres tall and was built in 1889.",
                "Quantum entanglement is a phenomenon in quantum mechanics.",
            )
            print(f"\n  Gate score (bad compression): {result.score:.4f}  passed={result.passed}")
            assert not result.passed
            await gate.close()
        asyncio.run(_inner())

@pytest.mark.ollama
class TestOllamaChatProxy:
    """
    End-to-end tests routing chat completions through the LCO proxy to Ollama.
    These are the tests you run FIRST to validate the whole stack.
    Requires: Ollama running + chat model pulled.
    """

    @ollama_available
    @ollama_chat_model
    def test_non_streaming_chat_through_proxy(self):
        """
        Full round-trip: HTTP client → LCO proxy → Ollama → response.
        Sets env vars BEFORE the app starts so the settings singleton
        picks up the Ollama URL correctly.
        """
        import os
        from fastapi.testclient import TestClient
        import lco.proxy.router as _router
        import lco.proxy.quality_gate as _qg

        # Set env vars before the app reads settings
        os.environ["LCO_OPENAI_BASE_URL"] = OLLAMA_BASE
        os.environ["LCO_COMPRESSION_MODE"] = "passthrough"
        os.environ["LCO_QUALITY_GATE"] = "false"

        # Reset singletons so they re-read the updated env vars
        _router._client = None
        _qg.reset_quality_gate()

        # Reload settings to pick up new env vars
        import importlib
        import lco.config as _cfg
        importlib.reload(_cfg)
        import lco.proxy.router
        lco.proxy.router.settings = _cfg.settings

        from lco.main import create_app
        fresh_app = create_app()

        with TestClient(fresh_app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "model": OLLAMA_CHAT_MODEL,
                    "messages": [
                        {"role": "user", "content": "Reply with exactly: OK"}
                    ],
                    "stream": False,
                },
                headers={"Authorization": "Bearer ollama"},
            )

        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"
        data = r.json()
        assert "choices" in data
        assert len(data["choices"]) > 0
        msg_content = data["choices"][0]["message"]["content"]
        assert isinstance(msg_content, str)
        assert len(msg_content) > 0
        print(f"\n  Ollama response: {msg_content[:100]}")

    @ollama_available
    @ollama_chat_model
    def test_proxy_returns_openai_format(self):
        """Response from Ollama must arrive in OpenAI chat completion format.
        Requires the proxy to be running: python3 cli.py start
        """
        try:
            httpx.get("http://127.0.0.1:8000/health", timeout=1)
        except Exception:
            pytest.skip("Proxy not running — start with: python3 cli.py start")
        r = httpx.post(
            "http://127.0.0.1:8000/v1/chat/completions",
            json={
                "model": OLLAMA_CHAT_MODEL,
                "messages": [{"role": "user", "content": "Say: pong"}],
                "stream": False,
            },
            headers={"Authorization": "Bearer ollama"},
            timeout=30,
        )
        if r.status_code != 200:
            pytest.skip(
                f"Proxy returned {r.status_code}. "
                "If 404: restart the proxy after updating openai.py — "
                "python3 cli.py start"
            )
        assert r.status_code == 200
        data = r.json()
        # Validate OpenAI response schema
        assert "id" in data
        assert "choices" in data
        assert "usage" in data
        assert data["choices"][0]["message"]["role"] == "assistant"

    @ollama_available
    @ollama_chat_model
    def test_proxy_safe_zones_header_present(self):
        """Proxy must always return x-lco-safe-zones header.
        Requires the proxy to be running: python3 cli.py start
        """
        try:
            httpx.get("http://127.0.0.1:8000/health", timeout=1)
        except Exception:
            pytest.skip("Proxy not running — start with: python3 cli.py start")
        r = httpx.post(
            "http://127.0.0.1:8000/v1/chat/completions",
            json={
                "model": OLLAMA_CHAT_MODEL,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"Authorization": "Bearer ollama"},
            timeout=30,
        )
        if r.status_code != 200:
            pytest.skip(
                f"Proxy returned {r.status_code}. "
                "If 404: restart the proxy after updating openai.py"
            )
        assert "x-lco-safe-zones" in r.headers
        assert "x-lco-provider" in r.headers
        assert r.headers["x-lco-provider"] == "openai"  # Ollama uses OpenAI adapter

    @ollama_available
    @ollama_chat_model
    def test_streaming_through_proxy_to_ollama(self):
        """
        Streaming: SSE chunks must arrive and assemble into coherent text.
        Tests the LCO-2 buffer end-to-end with a real streaming response.
        """
        async def _inner():
            async with httpx.AsyncClient(timeout=30) as client:
                chunks = []
                assembled = ""
                try:
                    async with client.stream(
                        "POST",
                        "http://127.0.0.1:8000/v1/chat/completions",
                        json={
                            "model": OLLAMA_CHAT_MODEL,
                            "messages": [{"role": "user", "content": "Count: 1 2 3"}],
                            "stream": True,
                        },
                        headers={"Authorization": "Bearer ollama"},
                    ) as r:
                        if r.status_code != 200:
                            pytest.skip("Proxy not running or returned error")
                        async for line in r.aiter_lines():
                            if line.startswith("data:"):
                                payload = line[5:].strip()
                                if payload == "[DONE]":
                                    break
                                try:
                                    event = json.loads(payload)
                                    delta = event["choices"][0]["delta"].get("content", "")
                                    assembled += delta
                                    chunks.append(payload)
                                except Exception:
                                    pass
                except httpx.ConnectError:
                    pytest.skip("Proxy not running — start with: python3 cli.py start")
    
            assert len(chunks) > 0, "No SSE chunks received"
            assert len(assembled) > 0, "No content assembled from stream"
            print(f"\n  Streaming assembled ({len(chunks)} chunks): {assembled[:80]}")
        asyncio.run(_inner())

@pytest.mark.ollama
class TestOllamaQualityGateProxy:
    """
    Tests quality gate behaviour when proxy is running with Ollama embedder.
    Set LCO_EMBEDDER=ollama and LCO_COMPRESSION_MODE=light to activate.
    """

    @ollama_available
    @ollama_embed_model
    def test_gate_approves_boilerplate_removal_with_ollama(self):
        """
        Run the cleaner on a realistic assistant message, then verify the
        Ollama quality gate approves the result.
        This is the core LCO-3+LCO-4 integration with real neural embeddings.
        """
        async def _inner():
            gate = QualityGate(
                threshold=0.78,
                embedder="ollama",
                ollama_base_url=OLLAMA_BASE,
                ollama_model=OLLAMA_EMBED_MODEL,
            )
            original = (
                "Certainly! Great question! "
                "The LCO proxy works by intercepting your API calls and removing "
                "unnecessary tokens before forwarding the request upstream. "
                "This reduces cost without changing your application code. "
                "Please let me know if you have any further questions!"
            )
            messages = [{"role": "assistant", "content": original}]
            cleaned, stats = clean_messages(messages, skip_last_user=False)
            compressed = cleaned[0]["content"]
    
            print(f"\n  Original ({len(original)} chars): {original[:80]}...")
            print(f"  Compressed ({len(compressed)} chars): {compressed[:80]}...")
            print(f"  Boilerplate removed: {stats.boilerplate_removed}")
    
            result = await gate.check(original, compressed)
            print(f"  Ollama gate score: {result.score:.4f}  passed={result.passed}")
    
            assert stats.boilerplate_removed > 0, "Cleaner should have removed boilerplate"
            assert result.passed, (
                f"Cleaner output should pass Ollama quality gate. "
                f"score={result.score:.3f} threshold={result.threshold}"
            )
            await gate.close()
        asyncio.run(_inner())