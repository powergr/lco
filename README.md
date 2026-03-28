# LCO — LLM Context Optimizer v0.2.0

> Local-first proxy that reduces LLM costs by compressing both input and
> output tokens — automatically, without changing your application code.

---

## Quick start

```bash
# 1. Clone and install
pip install -r requirements.txt
python3 install.py          # registers lco as importable package (run once)

# 2. Start via CLI
python3 cli.py start --openai-url http://localhost:11434 \
                     --mode aggressive --output-on

# 3. OR start via tray app (menu bar / system tray)
python3 tray.py --openai-url http://localhost:11434 \
                --mode aggressive --output-on

# 4. Point your client at LCO (one line change)
#    OpenAI SDK:    base_url="http://127.0.0.1:8000/v1"
#    Anthropic SDK: base_url="http://127.0.0.1:8000"

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

## Tray app (recommended for daily use)

The tray app runs the proxy silently in the background and puts a small
icon in your taskbar/menu bar. No terminal window needed.

### Install

```bash
pip install pystray Pillow

# Linux only — also needed:
sudo apt install python3-tk libappindicator3-1
```

### Run

```bash
# With Ollama
python3 tray.py --openai-url http://localhost:11434 \
                --mode aggressive --output-on

# With OpenRouter
python3 tray.py --openai-url https://openrouter.ai/api \
                --mode aggressive --output-on
```

### What you get

- **Tray icon** — green = running, grey = stopped. Coloured dot shows mode.
- **Right-click menu** — switch mode, toggle output compression, open popup, open dashboard, quit.
- **Status popup** — click "📊 Status & Savings..." for a live window showing:
  - 💰 Money saved this session (GPT-4o pricing reference)
  - All-time savings
  - Input/output tokens saved
  - Mode selector buttons
  - Output compression toggle

### Platform notes

| Platform | Icon location                                   | Extra steps                                                                                               |
| -------- | ----------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Windows  | Bottom-right taskbar (click `^` to show hidden) | None                                                                                                      |
| macOS    | Top-right menu bar                              | Use `pythonw tray.py` on Apple Silicon if icon doesn't appear                                             |
| Linux    | Depends on desktop                              | GNOME requires [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/) |

### Packaging as a standalone app

```bash
pip install pyinstaller
python3 build.py          # detects your platform automatically
```

**Windows** → `dist/LCO.exe` — single file, no installer needed. Copy and run.

**macOS** → `dist/LCO.app` — drag to `/Applications` and double-click.

```bash
# Optional: sign for Gatekeeper
codesign --deep --force --sign 'Developer ID Application: YOUR NAME' dist/LCO.app
```

**Linux** → `dist/LCO` — standalone binary. Run directly or wrap in AppImage:

```bash
chmod +x dist/LCO
./dist/LCO --openai-url http://localhost:11434 --mode aggressive
```

---

## CLI reference

`cli.py` handles both server management and runtime control.
**No `.env` file required** — every setting is a CLI flag.

### Starting the proxy

```bash
python3 cli.py start                                      # defaults
python3 cli.py start --openai-url http://localhost:11434  # Ollama
python3 cli.py start --openai-url https://openrouter.ai/api  # OpenRouter
python3 cli.py start --mode aggressive --output-on        # with compression
python3 cli.py start --daemon                             # background
python3 cli.py stop
```

### Runtime control (no restart needed)

```bash
python3 cli.py status
python3 cli.py mode aggressive
python3 cli.py output on
python3 cli.py memory on
python3 cli.py memory window 6
python3 cli.py gate threshold 0.40
python3 cli.py embedder ollama
python3 cli.py metrics
python3 cli.py metrics --reset
```

### Terminal metrics viewer

```bash
python3 view_metrics.py           # last 20 requests + summary
python3 view_metrics.py --limit 50
python3 view_metrics.py --summary
```

---

## Supported providers

All providers use `--openai-url`. Provider is auto-detected from model name,
API key format, or URL.

| Provider           | `--openai-url`                       | Model examples                                      |
| ------------------ | ------------------------------------ | --------------------------------------------------- |
| **Ollama** (local) | `http://localhost:11434`             | `llama3.2`, `qwen2.5:7b`                            |
| **OpenAI**         | `https://api.openai.com`             | `gpt-4o`, `gpt-4o-mini`                             |
| **OpenRouter**     | `https://openrouter.ai/api`          | `openai/gpt-4o`, `anthropic/claude-3-5-sonnet`      |
| **Groq**           | `https://api.groq.com/openai`        | `llama-3.3-70b-versatile`                           |
| **Mistral**        | `https://api.mistral.ai`             | `mistral-large-latest`                              |
| **Together AI**    | `https://api.together.xyz`           | `meta-llama/Llama-3-70b-chat-hf`                    |
| **DeepSeek**       | `https://api.deepseek.com`           | `deepseek-chat`, `deepseek-coder`                   |
| **Perplexity**     | `https://api.perplexity.ai`          | `llama-3.1-sonar-large-128k-online`                 |
| **Fireworks**      | `https://api.fireworks.ai/inference` | `accounts/fireworks/models/llama-v3p1-70b-instruct` |
| **Anthropic**      | via `--anthropic-url`                | `claude-opus-4-5`, `claude-sonnet-4-6`              |

---

## Testing with OpenRouter

```bash
python3 cli.py start \
  --openai-url https://openrouter.ai/api \
  --mode aggressive --output-on
```

```python
from openai import OpenAI
client = OpenAI(api_key="sk-or-...", base_url="http://127.0.0.1:8000/v1")
response = client.chat.completions.create(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": "Explain list comprehensions."}],
)
```

Response headers on every request:

| Header               | Meaning                             |
| -------------------- | ----------------------------------- |
| `x-lco-provider`     | Detected provider                   |
| `x-lco-mode`         | Active compression mode             |
| `x-lco-input-saved`  | Input tokens removed                |
| `x-lco-output-saved` | Output tokens removed               |
| `x-lco-safe-zones`   | Messages protected from compression |

---

## Testing with Ollama (no API key)

```bash
ollama pull qwen2.5:7b           # 4.7 GB, fits 6 GB VRAM — recommended
ollama pull nomic-embed-text     # for neural quality gate embeddings

python3 cli.py start \
  --openai-url http://localhost:11434 \
  --mode aggressive --output-on

python3 benchmark.py --mode aggressive --model qwen2.5:7b
python3 benchmark.py --mode aggressive --model qwen2.5:7b --verbose
```

---

## Benchmark

12 realistic conversations across 4 categories. Measures actual token
savings from LCO's response headers (single request per conversation —
no two-call comparison that would be corrupted by LLM non-determinism).

```bash
python3 benchmark.py                           # all categories, light mode
python3 benchmark.py --mode aggressive         # maximum compression
python3 benchmark.py --mode aggressive --verbose   # show input + savings
python3 benchmark.py --category docs           # prose-only (highest savings)
python3 benchmark.py --dry-run                 # estimate without LLM
```

| Category         | Content                              | Expected reduction |
| ---------------- | ------------------------------------ | ------------------ |
| Customer Support | Boilerplate-heavy exchanges          | 35–45%             |
| Data Analysis    | Verbose explanations with repetition | 38–50%             |
| Documentation    | Pure prose, no code                  | 45–55%             |
| Coding Assistant | Mixed prose + code blocks            | 30–40%             |

**Typical results (aggressive, qwen2.5:7b):**

```text
GRAND TOTAL (12)  ~40–47% total token reduction
```

---

## Connecting your client

```python
# OpenAI SDK
from openai import OpenAI
client = OpenAI(api_key="your-key", base_url="http://127.0.0.1:8000/v1")

# Anthropic SDK
from anthropic import Anthropic
client = Anthropic(api_key="sk-ant-...", base_url="http://127.0.0.1:8000")
```

```bash
# Claude Code
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 claude

# curl
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}'
```

---

## Compression modes

| Mode          | What it does                | Risk       | Typical reduction |
| ------------- | --------------------------- | ---------- | ----------------- |
| `passthrough` | No compression              | None       | 0%                |
| `light`       | Boilerplate removal + dedup | Very low   | 15–25%            |
| `medium`      | Light + sentence extraction | Low        | 30–45%            |
| `aggressive`  | Medium + LLM summarisation  | Low–medium | 40–55%            |

```bash
python3 cli.py output on    # also compress LLM responses
python3 cli.py memory on    # compress old conversation turns
```

---

## All `cli.py start` flags

```text
--host              Bind address (default: 127.0.0.1)
--port              Listen port (default: 8000)
--daemon            Run in background
--log-level         INFO | DEBUG | WARNING

--openai-url        OpenAI / Ollama / OpenRouter upstream URL
--anthropic-url     Anthropic upstream URL

--mode              passthrough | light | medium | aggressive
--output-on         Enable output compression
--memory-on         Enable memory compression
--memory-window     Turns to keep uncompressed (default: 8)
--gate-on           Enable quality gate (default: on)
--threshold         Similarity threshold: 0.40 TF-IDF · 0.80 Ollama
--embedder          tfidf | ollama | null

--ollama-embed-model     Embedding model (default: nomic-embed-text)
--ollama-compress-model  LLM compression model (default: qwen2.5:7b)
--llm-min-tokens    Min tokens before LLM compression fires (default: 200)
--db                SQLite metrics path (default: ./lco_metrics.db)
```

---

## Project structure

```text
lco/
├── tray.py                 ← menu bar / system tray app (recommended)
├── cli.py                  ← unified CLI: start · stop · mode · metrics
├── build.py                ← package tray app as .exe / .app / binary
├── build_windows.spec      ← PyInstaller spec for Windows
├── main.py                 ← FastAPI app factory
├── adapters.py             ← all 11 providers in one file
├── config.py               ← settings (env-var driven)
├── install.py              ← one-time dev setup
├── benchmark.py            ← 12-conversation benchmark
├── view_metrics.py         ← terminal metrics viewer
│
├── adapters/
│   └── __init__.py         ← compatibility shim → adapters.py
│
├── proxy/
│   ├── router.py           ← full pipeline
│   ├── safe_zones.py       ← code/JSON/tool-call exclusion
│   ├── buffer.py           ← streaming buffer (LCO-2)
│   ├── cleaner.py          ← boilerplate removal (LCO-3)
│   ├── compressor.py       ← sentence extraction (LCO-5)
│   ├── llm_compressor.py   ← Ollama summarisation
│   ├── output_optimizer.py ← output compression (LCO-6)
│   ├── memory.py           ← memory compression (LCO-7)
│   ├── quality_gate.py     ← TF-IDF + Ollama gate (LCO-4)
│   └── dashboard.py        ← web dashboard HTML
│
├── middleware/metrics.py   ← request timing
├── storage/metrics.py      ← SQLite (aiosqlite)
│
└── tests/
    ├── test_lco1.py        ← proxy core, adapters
    ├── test_lco2.py        ← streaming buffer
    ├── test_lco3.py        ← cleaner
    ├── test_lco4.py        ← quality gate + Ollama
    └── test_phase2.py      ← compressor, output, memory
```

---

## API routes

| Route                 | Description                                 |
| --------------------- | ------------------------------------------- |
| `GET  /health`        | Health check                                |
| `GET  /lco/status`    | Config + metrics JSON                       |
| `GET  /lco/recent`    | Last 20 requests                            |
| `GET  /lco/dashboard` | Web dashboard                               |
| `POST /lco/control`   | Runtime config (used by cli.py and tray.py) |
| `ANY  /v1/*`          | Proxy to upstream                           |

---

## Safe Zones — never modified

| Content               | Detection                                 |
| --------------------- | ----------------------------------------- |
| Tool / function calls | `tool_calls` array or `role: tool`        |
| Anthropic tool blocks | `content[].type == "tool_use"`            |
| Fenced code blocks    | ` ``` ` — prose around them is compressed |
| JSON-only responses   | Entire content parses as valid JSON       |

---

## Running tests

```bash
pytest tests/ -v                    # all (Ollama tests skip if not running)
pytest tests/ -v -m "not ollama"    # skip Ollama
```

Expected without Ollama: **174 passed, 12 skipped**

---

## Quality gate tuning

| Embedder          | Threshold | Use case                      |
| ----------------- | --------- | ----------------------------- |
| `tfidf` (default) | `0.40`    | No deps, fast, offline        |
| `ollama`          | `0.80`    | Better accuracy, needs Ollama |
| `null`            | —         | Disable gate                  |

```bash
python3 cli.py embedder ollama    # auto-sets threshold to 0.80
python3 cli.py embedder tfidf     # back to built-in
```

---

## Real-world benchmarks

[OpenRouter results](openrouter.md)

---

## Roadmap

| Component                                | Status |
| ---------------------------------------- | ------ |
| Proxy core + safe zones (LCO-1)          | ✅     |
| Streaming buffer (LCO-2)                 | ✅     |
| Input cleaner (LCO-3)                    | ✅     |
| Quality gate TF-IDF + Ollama (LCO-4)     | ✅     |
| Semantic extraction (LCO-5)              | ✅     |
| Output optimizer (LCO-6)                 | ✅     |
| Memory compression (LCO-7)               | ✅     |
| LLM-assisted compression                 | ✅     |
| Web dashboard                            | ✅     |
| 11 provider adapters                     | ✅     |
| System tray / menu bar app               | ✅     |
| Windows .exe / macOS .app / Linux binary | ✅     |
| LLMLingua / BERT compression             | V2     |
| Length-adaptive output quality gate      | V2     |
| Smart routing (local vs cloud)           | V2     |
| PyPI package                             | V2     |
| Docker image                             | V2     |

---

## License

MIT
