"""
LCO — Configuration
Loaded once at startup. All settings are env-var driven
so the proxy can be configured without touching code.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── Proxy ────────────────────────────────────────────────────────────────
    host: str = field(default_factory=lambda: os.getenv("LCO_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("LCO_PORT", "8000")))

    # ── Upstream providers ───────────────────────────────────────────────────
    openai_base_url: str = field(
        default_factory=lambda: os.getenv(
            "LCO_OPENAI_BASE_URL", "https://api.openai.com"
        )
    )
    anthropic_base_url: str = field(
        default_factory=lambda: os.getenv(
            "LCO_ANTHROPIC_BASE_URL", "https://api.anthropic.com"
        )
    )

    # ── Streaming ────────────────────────────────────────────────────────────
    # How long (ms) the buffer waits after stream end before processing.
    # Keeping this at 0 for MVP — passthrough only, no output compression yet.
    stream_flush_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("LCO_STREAM_FLUSH_MS", "0"))
    )

    # ── Quality gate ─────────────────────────────────────────────────────────
    quality_gate_enabled: bool = field(
        default_factory=lambda: os.getenv("LCO_QUALITY_GATE", "true").lower() == "true"
    )
    # TF-IDF threshold: 0.40 works for extractive compression (word overlap only)
    # Ollama/neural threshold: 0.80 measures actual semantics (set via LCO_QUALITY_THRESHOLD)
    quality_threshold: float = field(
        default_factory=lambda: float(os.getenv("LCO_QUALITY_THRESHOLD", "0.40"))
    )

    # ── Compression ──────────────────────────────────────────────────────────
    # MVP ships input compression as passthrough only.
    # Flip to "light" once E-02 lands.
    compression_mode: str = field(
        default_factory=lambda: os.getenv("LCO_COMPRESSION_MODE", "passthrough")
    )

    # ── Logging ──────────────────────────────────────────────────────────────
    log_level: str = field(
        default_factory=lambda: os.getenv("LCO_LOG_LEVEL", "INFO")
    )

    # ── Storage ──────────────────────────────────────────────────────────────
    db_path: str = field(
        default_factory=lambda: os.getenv("LCO_DB_PATH", "./lco_metrics.db")
    )

    # ── Embedder / Quality Gate ──────────────────────────────────────────────
    embedder: str = field(
        default_factory=lambda: os.getenv("LCO_EMBEDDER", "tfidf")
    )
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("LCO_OLLAMA_BASE_URL", "http://localhost:11434")
    )
    ollama_embed_model: str = field(
        default_factory=lambda: os.getenv("LCO_OLLAMA_EMBED_MODEL", "nomic-embed-text")
    )
    ollama_embed_timeout: float = field(
        default_factory=lambda: float(os.getenv("LCO_OLLAMA_EMBED_TIMEOUT", "60"))
    )
    # LLM-assisted compression (uses Ollama to summarise long messages)
    ollama_compress_model: str = field(
        default_factory=lambda: os.getenv("LCO_OLLAMA_COMPRESS_MODEL", "qwen2.5:7b")
    )
    ollama_compress_timeout: float = field(
        default_factory=lambda: float(os.getenv("LCO_OLLAMA_COMPRESS_TIMEOUT", "30"))
    )
    llm_compress_min_tokens: int = field(
        default_factory=lambda: int(os.getenv("LCO_LLM_COMPRESS_MIN_TOKENS", "200"))
    )

    # ── Output optimization ──────────────────────────────────────────────────
    output_optimization: bool = field(
        default_factory=lambda: os.getenv("LCO_OUTPUT_OPT", "false").lower() == "true"
    )

    # ── Memory compression ────────────────────────────────────────────────────
    memory_window: int = field(
        default_factory=lambda: int(os.getenv("LCO_MEMORY_WINDOW", "8"))
    )
    memory_compression: bool = field(
        default_factory=lambda: os.getenv("LCO_MEMORY_COMPRESSION", "false").lower() == "true"
    )
    memory_inject_summary: bool = field(
        default_factory=lambda: os.getenv("LCO_MEMORY_SUMMARY", "true").lower() == "true"
    )

    # ── Request timeout (seconds) ────────────────────────────────────────────
    upstream_timeout: int = field(
        default_factory=lambda: int(os.getenv("LCO_UPSTREAM_TIMEOUT", "120"))
    )


# Singleton — import this everywhere
settings = Config()