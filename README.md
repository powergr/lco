# LCO — LLM Context Optimizer v0.2.0

> Local-first, OpenAI-compatible proxy that reduces LLM costs by optimising
> both input and output tokens — automatically, without changing your code.

---

## How it works

```text
Your app  →  LCO (localhost:8000)  →  OpenAI / Anthropic / Ollama
                     ↑
    memory compress → clean → semantic compress → quality gate
    ← output compress ← quality gate ← buffer ←
```

You change one line. LCO handles the rest.

---

## Quick start

```bash
# 1. Install
pip install -r requirements.txt
python3 install.py          # registers lco as importable package

# 2. Configure (edit .env as needed — defaults are safe)
# 3. Start
python3 cli.py start

# 4. Point your client at LCO
#    OpenAI:    base_url="http://127.0.0.1:8000/v1"
#    Anthropic: base_url="http://127.0.0.1:8000"
#    Claude Code: ANTHROPIC_BASE_URL=http://127.0.0.1:8000 claude
```

---

## Testing with Ollama (no API keys needed)

```bash
ollama serve
ollama pull llama3.2           # ~2 GB, for chat
ollama pull nomic-embed-text   # ~274 MB, for quality gate embeddings
```

Edit `.env`:

```env
LCO_OPENAI_BASE_URL=http://localhost:11434
```

```bash
python3 cli.py start
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer ollama" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"Hello"}]}'
```

---

## Dashboard

Open in browser while proxy is running:

```html
http://127.0.0.1:8000/lco/dashboard
```

Shows: request KPIs, latency chart, compression config, recent requests table.
Refreshes every 3 seconds automatically.

---

## Enabling compression

All features are **off by default**. Enable gradually via `.env`:

```env
# Step 1 — safe, immediate value: remove boilerplate from inputs
LCO_COMPRESSION_MODE=light

# Step 2 — compress LLM responses too
LCO_OUTPUT_OPT=true

# Step 3 — semantic sentence extraction (medium or aggressive)
LCO_COMPRESSION_MODE=medium

# Step 4 — memory compression for long conversations
LCO_MEMORY_COMPRESSION=true
LCO_MEMORY_WINDOW=8         # keep last 8 turns uncompressed

# Step 5 — neural embeddings for better quality gate accuracy
LCO_EMBEDDER=ollama
LCO_OLLAMA_EMBED_MODEL=nomic-embed-text
```

Restart the proxy after changing `.env`.

---

## Project structure

```text
lco/
├── cli.py                      start / status / stop
├── main.py                     FastAPI app factory
├── config.py                   all settings (env-var driven)
├── install.py                  one-time setup (writes .pth file)
├── view_metrics.py             terminal metrics viewer
├── conftest.py                 pytest path fix
├── pytest.ini                  test markers
├── requirements.txt
├── .env                        live config (edit this)
├── .env.example                template
│
├── proxy/
│   ├── router.py               full Phase 2 pipeline
│   ├── safe_zones.py           hard-exclusion rules
│   ├── buffer.py               LCO-2: streaming buffer
│   ├── cleaner.py              LCO-3: boilerplate removal + dedup
│   ├── compressor.py           LCO-5: semantic sentence extraction
│   ├── output_optimizer.py     LCO-6: output compression
│   ├── memory.py               LCO-7: conversation memory compression
│   ├── quality_gate.py         LCO-4: TF-IDF / Ollama similarity gate
│   └── dashboard.py            local web dashboard HTML
│
├── adapters/
│   ├── base.py                 adapter interface
│   ├── openai.py               OpenAI + Ollama (passthrough)
│   └── anthropic.py            Anthropic ↔ OpenAI translation
│
├── middleware/
│   └── metrics.py              request timing
│
├── storage/
│   └── metrics.py              SQLite metrics (aiosqlite)
│
└── tests/
    ├── test_lco1.py            proxy core, safe zones, adapters (35 tests)
    ├── test_lco2.py            streaming buffer (24 tests)
    ├── test_lco3.py            cleaner + streaming integration (43 tests)
    ├── test_lco4.py            quality gate + Ollama (46 tests)
    └── test_phase2.py          compressor, output, memory, pipeline (53 tests)
```

---

## CLI

```bash
python3 cli.py start                    # foreground
python3 cli.py start --daemon           # background
python3 cli.py start --port 9000        # custom port
python3 cli.py start --log-level DEBUG  # verbose
python3 cli.py status                   # live metrics
python3 cli.py stop                     # stop daemon

python3 view_metrics.py                 # terminal metrics viewer
python3 view_metrics.py --limit 50      # last 50 requests
python3 view_metrics.py --summary       # summary only
```

---

## All environment variables

| Variable                   | Default                     | Description                                       |
| -------------------------- | --------------------------- | ------------------------------------------------- |
| `LCO_HOST`                 | `127.0.0.1`                 | Bind address                                      |
| `LCO_PORT`                 | `8000`                      | Listen port                                       |
| `LCO_OPENAI_BASE_URL`      | `https://api.openai.com`    | OpenAI / Ollama upstream                          |
| `LCO_ANTHROPIC_BASE_URL`   | `https://api.anthropic.com` | Anthropic upstream                                |
| `LCO_COMPRESSION_MODE`     | `passthrough`               | `passthrough` · `light` · `medium` · `aggressive` |
| `LCO_OUTPUT_OPT`           | `false`                     | Compress LLM responses                            |
| `LCO_MEMORY_COMPRESSION`   | `false`                     | Compress old conversation turns                   |
| `LCO_MEMORY_WINDOW`        | `8`                         | Turns to keep uncompressed                        |
| `LCO_MEMORY_SUMMARY`       | `true`                      | Inject summary of compressed turns                |
| `LCO_QUALITY_GATE`         | `true`                      | Enable similarity gate                            |
| `LCO_QUALITY_THRESHOLD`    | `0.85`                      | Min similarity score (0–1)                        |
| `LCO_EMBEDDER`             | `tfidf`                     | `tfidf` · `ollama` · `null`                       |
| `LCO_OLLAMA_BASE_URL`      | `http://localhost:11434`    | Ollama server                                     |
| `LCO_OLLAMA_EMBED_MODEL`   | `nomic-embed-text`          | Embedding model                                   |
| `LCO_OLLAMA_EMBED_TIMEOUT` | `60`                        | Embed request timeout (s)                         |
| `LCO_OLLAMA_CHAT_MODEL`    | `llama3.2`                  | Chat model (tests only)                           |
| `LCO_DB_PATH`              | `./lco_metrics.db`          | SQLite file                                       |
| `LCO_UPSTREAM_TIMEOUT`     | `120`                       | Upstream timeout (s)                              |
| `LCO_LOG_LEVEL`            | `INFO`                      | `DEBUG` · `INFO` · `WARNING`                      |

---

## API routes

| Route                | Description                            |
| -------------------- | -------------------------------------- |
| `GET /health`        | Health check                           |
| `GET /lco/status`    | Full metrics + config (JSON)           |
| `GET /lco/recent`    | Last 20 requests (JSON, for dashboard) |
| `GET /lco/dashboard` | Web dashboard                          |
| `GET /lco/docs`      | Swagger UI                             |
| `ANY /v1/*`          | Proxy to upstream                      |

Response headers on every proxied request:

- `x-lco-provider` — detected provider
- `x-lco-safe-zones` — count of protected messages
- `x-lco-mode` — active compression mode
- `x-lco-buffer` — `enabled` on streaming responses

---

## Running tests

```bash
pytest tests/ -v                        # all tests (Ollama tests skip if not running)
pytest tests/ -v -m ollama              # only Ollama tests
pytest tests/ -v -m "not ollama"        # skip Ollama tests

# Ollama tests require:
#   ollama serve
#   ollama pull llama3.2
#   ollama pull nomic-embed-text
```

Expected (without Ollama): **173 passed, 12 skipped**

---

## Safe Zones — content never modified

| Content                      | Detection                        |
| ---------------------------- | -------------------------------- |
| Tool / function call inputs  | `tool_calls` array               |
| Tool / function call outputs | `role: tool` or `role: function` |
| Anthropic tool blocks        | `content[].type == "tool_use"`   |
| Fenced code blocks           | ` ``` ` pairs                    |
| JSON-only payloads           | Entire content parses as JSON    |
| Explicitly protected         | `<!-- lco-safe -->` annotation   |

---

## Roadmap

| Epic                                   | Status |
| -------------------------------------- | ------ |
| LCO-1 Proxy core, adapters, Safe Zones | ✅     |
| LCO-2 Streaming buffer                 | ✅     |
| LCO-3 Input cleaner + deduplicator     | ✅     |
| LCO-4 Quality gate (TF-IDF + Ollama)   | ✅     |
| LCO-5 Semantic sentence extraction     | ✅     |
| LCO-6 Output optimizer                 | ✅     |
| LCO-7 Memory compression               | ✅     |
| Dashboard                              | ✅     |
| LLMLingua / BERT compression           | V2     |
| Ollama / local LLM smart routing       | V2     |
| Cloud-hosted tier                      | V2     |

---

## License

MIT
