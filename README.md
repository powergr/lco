# LCO — LLM Context Optimizer v0.2.0

> Local-first proxy that reduces LLM costs by compressing both input and
> output tokens — automatically, without changing your application code.

---

## Quick start

```bash
# 1. Clone and install
pip install -r requirements.txt
python3 install.py          # registers lco as importable package (run once)

# 2. Start — point at Ollama (no API keys needed)
python3 cli.py start --openai-url http://localhost:11434 \
                     --mode aggressive --output-on

# 3. Point your client at LCO (one line change)
#    OpenAI SDK:    base_url="http://127.0.0.1:8000/v1"
#    Anthropic SDK: base_url="http://127.0.0.1:8000"

# 4. Run tests
pytest tests/ -v

# 5. View dashboard
#    http://127.0.0.1:8000/lco/dashboard
```

---

## How it works

```text
Your app ──► LCO proxy (localhost:8000) ──► Upstream LLM API
                        │
          ┌─────────────▼──────────────────┐
          │ 1. Memory compression (LCO-7)  │ compress old turns
          │ 2. Input cleaner    (LCO-3)    │ remove boilerplate
          │ 3. Semantic compress (LCO-5)   │ sentence extraction
          │ 4. LLM compress               │ Ollama summarisation
          │ 5. Quality gate     (LCO-4)   │ similarity check
          │ 6. Forward to upstream         │
          │ 7. Output compress  (LCO-6)   │ compress response
          │ 8. Output quality gate         │ safety check
          └────────────────────────────────┘
```

Everything is opt-in. Default mode is `passthrough` — zero compression,
100% compatible with any existing client.

---

## CLI reference

LCO uses a single `cli.py` file for both server management and runtime
control. **No `.env` file required** — every setting is a CLI flag.

### Starting the proxy

```bash
# Minimum — uses defaults (OpenAI upstream, passthrough mode)
python3 cli.py start

# Ollama (local, no API key)
python3 cli.py start --openai-url http://localhost:11434

# Ollama with aggressive compression
python3 cli.py start --openai-url http://localhost:11434 \
                     --mode aggressive --output-on

# OpenRouter
python3 cli.py start --openai-url https://openrouter.ai/api

# Anthropic
python3 cli.py start --anthropic-url https://api.anthropic.com

# Background mode
python3 cli.py start --daemon
python3 cli.py stop

# All options
python3 cli.py start --help
```

### Runtime control (no restart needed)

```bash
python3 cli.py status                    # show config + metrics
python3 cli.py mode aggressive           # set compression mode
python3 cli.py output on                 # enable output compression
python3 cli.py memory on                 # enable memory compression
python3 cli.py memory window 6           # set memory window turns
python3 cli.py gate on                   # enable quality gate
python3 cli.py gate threshold 0.40       # set similarity threshold
python3 cli.py embedder ollama           # switch to neural embeddings
python3 cli.py metrics                   # show recent requests table
python3 cli.py metrics --reset           # clear all metrics
```

### View metrics in terminal

```bash
python3 view_metrics.py                  # last 20 requests + summary
python3 view_metrics.py --limit 50       # last 50 requests
python3 view_metrics.py --summary        # aggregate only
```

---

## Supported providers

All providers use the same `--openai-url` flag. LCO detects the correct
adapter automatically from the model name, API key format, or URL.

| Provider           | `--openai-url`                       | Model examples                                      |
| ------------------ | ------------------------------------ | --------------------------------------------------- |
| **Ollama** (local) | `http://localhost:11434`             | `llama3.2`, `qwen2.5:7b`                            |
| **OpenAI**         | `https://api.openai.com`             | `gpt-4o`, `gpt-4o-mini`                             |
| **OpenRouter**     | `https://openrouter.ai/api`          | `openai/gpt-4o`, `anthropic/claude-3-5-sonnet`      |
| **Groq**           | `https://api.groq.com/openai`        | `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`     |
| **Mistral**        | `https://api.mistral.ai`             | `mistral-large-latest`, `codestral-latest`          |
| **Together AI**    | `https://api.together.xyz`           | `meta-llama/Llama-3-70b-chat-hf`                    |
| **DeepSeek**       | `https://api.deepseek.com`           | `deepseek-chat`, `deepseek-coder`                   |
| **Perplexity**     | `https://api.perplexity.ai`          | `llama-3.1-sonar-large-128k-online`                 |
| **Fireworks**      | `https://api.fireworks.ai/inference` | `accounts/fireworks/models/llama-v3p1-70b-instruct` |
| **Anthropic**      | via `--anthropic-url`                | `claude-opus-4-5`, `claude-sonnet-4-6`              |

---

## Testing with OpenRouter

OpenRouter is the fastest way to test LCO against real frontier models
without separate API keys for each provider.

**1. Get an API key:** [Test with OpenRouter](https://openrouter.ai/keys)

**2. Start LCO pointing at OpenRouter:**

```bash
python3 cli.py start \
  --openai-url https://openrouter.ai/api \
  --mode aggressive \
  --output-on
```

**3. Make a request:**

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-or-...",                      # your OpenRouter key
    base_url="http://127.0.0.1:8000/v1",      # LCO proxy
)

response = client.chat.completions.create(
    model="openai/gpt-4o-mini",               # or any OpenRouter model
    messages=[{"role": "user", "content": "Explain list comprehensions."}],
)
print(response.choices[0].message.content)
```

**4. Or with curl:**

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-or-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

**5. Check what LCO did:**

```bash
python3 cli.py status
# or open: http://127.0.0.1:8000/lco/dashboard
```

Response headers on every proxied request:

| Header               | Meaning                                   |
| -------------------- | ----------------------------------------- |
| `x-lco-provider`     | Detected provider (`openai`, `anthropic`) |
| `x-lco-mode`         | Active compression mode                   |
| `x-lco-safe-zones`   | Messages protected from compression       |
| `x-lco-input-saved`  | Input tokens removed                      |
| `x-lco-output-saved` | Output tokens removed                     |
| `x-lco-buffer`       | `enabled` on streaming responses          |

---

## Testing with Ollama (no API key)

```bash
# Install Ollama: https://ollama.com
ollama pull qwen2.5:7b           # recommended — 4.7 GB, 6 GB VRAM
ollama pull nomic-embed-text     # for Ollama quality gate embeddings

ollama serve                     # start Ollama (separate terminal)

python3 cli.py start \
  --openai-url http://localhost:11434 \
  --mode aggressive \
  --output-on

# Run the benchmark
python3 benchmark.py --mode aggressive --model qwen2.5:7b
python3 benchmark.py --mode aggressive --model qwen2.5:7b --verbose
```

---

## Benchmark

Tests 12 realistic conversations across 4 categories and measures actual
token savings from LCO's response headers.

```bash
python3 benchmark.py                          # all categories, light mode
python3 benchmark.py --mode aggressive        # maximum compression
python3 benchmark.py --mode aggressive --verbose  # show input + savings per convo
python3 benchmark.py --category docs          # prose-only (highest savings)
python3 benchmark.py --category coding        # mixed prose+code
python3 benchmark.py --dry-run               # estimated numbers, no LLM needed
```

**Benchmark categories:**

| Category         | Content                              | Expected reduction |
| ---------------- | ------------------------------------ | ------------------ |
| Customer Support | Boilerplate-heavy exchanges          | 35–45%             |
| Data Analysis    | Verbose explanations with repetition | 38–50%             |
| Documentation    | Pure prose (no code)                 | 45–55%             |
| Coding Assistant | Mixed prose + code blocks            | 30–40%             |

**Typical results (aggressive mode, qwen2.5:7b):**

```text
GRAND TOTAL (12)  ~40–47% total token reduction
```

---

## Connecting your client

### OpenAI SDK (Python)

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-key",
    base_url="http://127.0.0.1:8000/v1",   # ← only change
)
```

### Anthropic SDK (Python)

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="sk-ant-...",
    base_url="http://127.0.0.1:8000",      # ← only change (no /v1)
)
```

### Claude Code

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 claude
```

### curl (generic)

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}'
```

---

## Compression modes

| Mode          | What it does                        | Risk       | Typical reduction |
| ------------- | ----------------------------------- | ---------- | ----------------- |
| `passthrough` | No compression — safe default       | None       | 0%                |
| `light`       | Boilerplate removal + deduplication | Very low   | 15–25%            |
| `medium`      | Light + sentence extraction         | Low        | 30–45%            |
| `aggressive`  | Medium + LLM summarisation          | Low–medium | 40–55%            |

Enable output compression on top of any mode:

```bash
python3 cli.py output on
```

---

## All start flags

```text
--host              Bind address (default: 127.0.0.1)
--port              Listen port (default: 8000)
--daemon            Run in background
--log-level         INFO | DEBUG | WARNING (default: INFO)

--openai-url        OpenAI/Ollama/OpenRouter upstream URL
--anthropic-url     Anthropic upstream URL

--mode              passthrough | light | medium | aggressive
--output-on         Enable output compression
--memory-on         Enable memory compression for long conversations
--memory-window     Turns to keep uncompressed (default: 8)
--gate-on / --no-gate   Enable/disable quality gate
--threshold         Similarity threshold — 0.40 for TF-IDF, 0.80 for Ollama
--embedder          tfidf | ollama | null

--ollama-embed-model    Embedding model (default: nomic-embed-text)
--ollama-compress-model LLM compression model (default: qwen2.5:7b)
--llm-min-tokens    Min tokens before LLM compression fires (default: 200)
--db                SQLite metrics path (default: ./lco_metrics.db)
```

---

## Project structure

```ascii
lco/
├── cli.py                  ← unified CLI: start · stop · mode · status · metrics
├── main.py                 ← FastAPI app factory
├── adapters.py             ← all 11 providers in one file
├── config.py               ← settings (reads from env vars set by cli.py)
├── install.py              ← one-time setup (writes .pth file)
├── benchmark.py            ← 12-conversation benchmark with --verbose
├── view_metrics.py         ← terminal metrics viewer
├── conftest.py             ← pytest path fix
├── pytest.ini              ← test markers
├── requirements.txt
│
├── adapters/
│   └── __init__.py         ← compatibility shim → adapters.py
│
├── proxy/
│   ├── router.py           ← full pipeline: memory→clean→compress→gate→forward→output
│   ├── safe_zones.py       ← hard-exclusion rules (code, JSON, tool calls)
│   ├── buffer.py           ← streaming buffer (LCO-2)
│   ├── cleaner.py          ← boilerplate removal + deduplication (LCO-3)
│   ├── compressor.py       ← sentence extraction compressor (LCO-5)
│   ├── llm_compressor.py   ← LLM-assisted summarisation via Ollama
│   ├── output_optimizer.py ← output compression (LCO-6)
│   ├── memory.py           ← conversation memory compression (LCO-7)
│   ├── quality_gate.py     ← TF-IDF + Ollama similarity gate (LCO-4)
│   └── dashboard.py        ← web dashboard HTML
│
├── middleware/
│   └── metrics.py          ← request timing
│
├── storage/
│   └── metrics.py          ← SQLite metrics (aiosqlite)
│
└── tests/
    ├── test_lco1.py        ← proxy core, safe zones, adapters
    ├── test_lco2.py        ← streaming buffer
    ├── test_lco3.py        ← cleaner + streaming integration
    ├── test_lco4.py        ← quality gate + Ollama
    └── test_phase2.py      ← compressor, output, memory, pipeline
```

---

## API routes

| Route                 | Description                            |
| --------------------- | -------------------------------------- |
| `GET  /health`        | Health check                           |
| `GET  /lco/status`    | Full config + metrics (JSON)           |
| `GET  /lco/recent`    | Last 20 real requests (JSON)           |
| `GET  /lco/dashboard` | Web dashboard                          |
| `GET  /lco/docs`      | Swagger UI                             |
| `POST /lco/control`   | Runtime config change (used by cli.py) |
| `ANY  /v1/*`          | Proxy to upstream                      |

---

## Safe Zones — content never modified

| Content                      | Detection                                       |
| ---------------------------- | ----------------------------------------------- |
| Tool / function call inputs  | `tool_calls` array on assistant message         |
| Tool / function call outputs | `role: tool` or `role: function`                |
| Anthropic tool blocks        | `content[].type == "tool_use"`                  |
| Fenced code blocks           | ` ``` ` pairs — prose around them is compressed |
| JSON-only responses          | Entire content parses as valid JSON             |
| Explicitly protected         | `<!-- lco-safe -->` annotation                  |

Note: for mixed prose+code responses, code blocks are preserved exactly
while prose sections before, between, and after them are compressed.

---

## Running tests

```bash
pytest tests/ -v                      # all tests
pytest tests/ -v -m "not ollama"      # skip Ollama-dependent tests
pytest tests/ -v -m ollama            # only Ollama tests

# Ollama tests require:
ollama serve
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

Expected (without Ollama running): **174 passed, 12 skipped**

---

## Quality gate tuning

The quality gate compares original vs compressed text using cosine
similarity and reverts to the original if the score is too low.

| Embedder          | Threshold | When to use                                             |
| ----------------- | --------- | ------------------------------------------------------- |
| `tfidf` (default) | `0.40`    | No dependencies, fast, works offline                    |
| `ollama`          | `0.80`    | Better semantic accuracy, requires Ollama + embed model |
| `null`            | —         | Disables gate entirely (not recommended for production) |

Switch embedder at runtime:

```bash
python3 cli.py embedder ollama    # auto-sets threshold to 0.80
python3 cli.py embedder tfidf     # back to built-in
```

---

## Roadmap

| Component                            | Status |
| ------------------------------------ | ------ |
| Proxy core + safe zones (LCO-1)      | ✅     |
| Streaming buffer (LCO-2)             | ✅     |
| Input cleaner (LCO-3)                | ✅     |
| Quality gate TF-IDF + Ollama (LCO-4) | ✅     |
| Semantic sentence extraction (LCO-5) | ✅     |
| Output optimizer (LCO-6)             | ✅     |
| Memory compression (LCO-7)           | ✅     |
| LLM-assisted compression             | ✅     |
| Web dashboard                        | ✅     |
| 11 provider adapters                 | ✅     |
| LLMLingua / BERT compression         | V2     |
| Smart routing (local vs cloud)       | V2     |
| PyPI package                         | V2     |
| Docker image                         | V2     |

---

## License

MIT
