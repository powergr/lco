# LCO — LLM Context Optimizer v0.2.4

> Local-first proxy that reduces LLM costs by compressing both input and
> output tokens — automatically, without changing your application code.

---

## Quick start

```bash
pip install -r requirements.txt
python3 install.py          # one-time: registers lco as importable package

# Option A — tray app (recommended, no terminal needed)
python3 tray.py

# Option B — CLI
python3 cli.py start --openai-url http://localhost:11434 --mode aggressive --output-on
```

Point your client at LCO:

```python
# OpenAI SDK
client = OpenAI(api_key="your-key", base_url="http://127.0.0.1:8000/v1")

# Anthropic SDK
client = Anthropic(api_key="sk-ant-...", base_url="http://127.0.0.1:8000")

# Claude Code
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 claude
```

---

## How it works

```ascii
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

Default mode is `passthrough` — zero compression, 100% compatible.

---

## Tray app (recommended)

The tray app runs completely without a terminal. Double-click `LCO.exe`
(Windows) or `LCO.app` (macOS) and a tray icon appears.

### Install dependencies (source only — not needed for .exe/.app)

```bash
pip install pystray Pillow
# Linux only:
sudo apt install python3-tk libappindicator3-1
```

### Data files location

All data is stored in a platform-appropriate folder — never beside the exe:

| Platform | Location                             |
| -------- | ------------------------------------ |
| Windows  | `%APPDATA%\LCO\`                     |
| macOS    | `~/Library/Application Support/LCO/` |
| Linux    | `~/.local/share/LCO/`                |

Contents: `settings.json`, `lco_metrics.db`, `lco.log`

### Tray features

**Right-click menu:**

- 💰 Live "X saved this session" counter
- Mode switcher (Off / Light / Medium / Max)
- Output compression toggle (saved immediately)
- 📊 Status & Savings popup
- ⚙ Settings window
- 🌐 Open Dashboard

**Status popup:**

- Session and all-time dollar savings
- Token breakdown (input saved / output saved / total requests)
- **📋 Copy proxy URL** — one click copies `http://127.0.0.1:8000/v1`
- Mode buttons and output toggle
- Link to Settings and Dashboard

**Settings window:**

- Provider selector with auto-fill URL
- **Separate API key fields** for every provider (OpenAI, Anthropic,
  OpenRouter, Groq, Mistral) — stored securely in `settings.json`
- Show/hide all keys toggle
- Model dropdown (pre-populated per provider) + free-text override
- **▶ Test connection** — sends a live request and shows latency
- Compression mode, output compression, memory compression
- Listen port + **Start with Windows** checkbox (Windows only)
- Save & Apply — applies runtime settings immediately, no restart needed

### UX safety features

| Feature                     | Behaviour                                                                  |
| --------------------------- | -------------------------------------------------------------------------- |
| **Single instance**         | If LCO is already running, shows a notification and exits cleanly          |
| **Port conflict detection** | If port 8000 is taken, offers to use the next free port automatically      |
| **Startup failure dialog**  | If proxy doesn't start within 10s, shows an error dialog with instructions |
| **First-run wizard**        | Settings window opens automatically on first launch                        |

---

## Packaging as a standalone app

```bash
pip install pyinstaller

# Detects platform automatically
python build.py
```

**Windows** → `dist\LCO.exe` (single file, no installer needed)

**macOS** → `dist/LCO.app` (drag to Applications)

**Linux** → `dist/LCO` (standalone binary)

### Windows installer (NSIS)

Produces `LCO-Setup-0.2.0.exe` with Start Menu, optional Desktop shortcut,
Add/Remove Programs entry, and Windows startup registration.

```bash
# Install NSIS: https://nsis.sourceforge.io
python build.py          # build LCO.exe first
makensis installer.nsi   # build the installer
```

To embed a custom icon, create `assets\lco.ico` and uncomment in both
`build_windows.spec` and `installer.nsi`:

```text
# build_windows.spec:
icon=str(ROOT / 'assets' / 'lco.ico')

# installer.nsi:
!define MUI_ICON "assets\lco.ico"
```

---

## CLI reference

```bash
python3 cli.py start --openai-url http://localhost:11434 --mode aggressive --output-on
python3 cli.py status
python3 cli.py mode aggressive
python3 cli.py output on
python3 cli.py memory on
python3 cli.py gate threshold 0.40
python3 cli.py metrics
python3 cli.py metrics --reset
python3 cli.py stop
```

---

## Supported providers

| Provider        | Upstream URL                  | Notes                       |
| --------------- | ----------------------------- | --------------------------- |
| **Ollama**      | `http://localhost:11434`      | No API key needed           |
| **OpenAI**      | `https://api.openai.com`      |                             |
| **Anthropic**   | `https://api.anthropic.com`   | Uses separate anthropic key |
| **OpenRouter**  | `https://openrouter.ai/api`   | Access to all models        |
| **Groq**        | `https://api.groq.com/openai` | Fastest inference           |
| **Mistral**     | `https://api.mistral.ai`      |                             |
| **Together AI** | `https://api.together.xyz`    |                             |
| **DeepSeek**    | `https://api.deepseek.com`    |                             |

---

## Benchmark

```bash
python3 benchmark.py --mode aggressive --model qwen2.5:7b
python3 benchmark.py --mode aggressive --verbose   # show input + savings
python3 benchmark.py --dry-run                     # estimate, no LLM needed
```

Typical results (aggressive mode): **40–47% total token reduction**

| Category         | Reduction |
| ---------------- | --------- |
| Customer Support | 35–45%    |
| Data Analysis    | 38–50%    |
| Documentation    | 45–55%    |
| Coding Assistant | 30–40%    |

---

## Response headers

Every proxied request includes:

| Header               | Meaning                    |
| -------------------- | -------------------------- |
| `x-lco-mode`         | Active compression mode    |
| `x-lco-input-saved`  | Input tokens removed       |
| `x-lco-output-saved` | Output tokens removed      |
| `x-lco-safe-zones`   | Messages protected         |
| `x-lco-provider`     | Detected upstream provider |

---

## Project structure

```aascii
lco/
├── tray.py                 ← standalone tray app (main entry point)
├── cli.py                  ← CLI: start · stop · mode · metrics
├── build.py                ← package as .exe / .app / binary
├── build_windows.spec      ← PyInstaller Windows spec (no UPX)
├── installer.nsi           ← NSIS Windows installer script
├── main.py                 ← FastAPI app factory
├── version.py              ← single version source of truth
├── adapters.py             ← all 11 providers in one file
├── config.py               ← settings (env-var driven)
├── benchmark.py            ← 12-conversation benchmark
├── view_metrics.py         ← terminal metrics viewer
│
├── proxy/
│   ├── router.py           ← full compression pipeline
│   ├── safe_zones.py       ← code/JSON/tool-call exclusion
│   ├── buffer.py           ← streaming buffer
│   ├── cleaner.py          ← boilerplate removal
│   ├── compressor.py       ← sentence extraction
│   ├── llm_compressor.py   ← Ollama summarisation
│   ├── output_optimizer.py ← output compression
│   ├── memory.py           ← memory compression
│   ├── quality_gate.py     ← TF-IDF + Ollama similarity gate
│   └── dashboard.py        ← web dashboard HTML
│
├── storage/metrics.py      ← SQLite metrics
├── middleware/metrics.py   ← request timing
│
└── tests/                  ← 174 passing tests
```

---

## API routes

| Route                 | Description                           |
| --------------------- | ------------------------------------- |
| `GET  /health`        | Health check                          |
| `GET  /lco/status`    | Config + metrics JSON                 |
| `GET  /lco/recent`    | Last 20 requests                      |
| `GET  /lco/dashboard` | Web dashboard                         |
| `POST /lco/control`   | Runtime config (used by tray and CLI) |
| `ANY  /v1/*`          | Proxy to upstream                     |

---

## Compression modes

| Mode          | What it does                | Risk       | Reduction |
| ------------- | --------------------------- | ---------- | --------- |
| `passthrough` | No compression              | None       | 0%        |
| `light`       | Boilerplate removal + dedup | Very low   | 15–25%    |
| `medium`      | Light + sentence extraction | Low        | 30–45%    |
| `aggressive`  | Medium + LLM summarisation  | Low–medium | 40–55%    |

---

## Quality gate

| Embedder          | Threshold | Use case               |
| ----------------- | --------- | ---------------------- |
| `tfidf` (default) | `0.40`    | No deps, fast, offline |
| `ollama`          | `0.80`    | Better accuracy        |
| `null`            | —         | Disable gate           |

---

## Running tests

```bash
pytest tests/ -v                   # all tests
pytest tests/ -v -m "not ollama"   # skip Ollama tests
```

Expected without Ollama: **174 passed, 12 skipped**

---

## Roadmap

| Feature                                  | Status |
| ---------------------------------------- | ------ |
| Proxy core + pipeline (LCO 1–7)          | ✅     |
| 11 provider adapters                     | ✅     |
| Web dashboard                            | ✅     |
| System tray / menu bar app               | ✅     |
| Settings persistence (no CLI needed)     | ✅     |
| Separate API keys per provider           | ✅     |
| Connection test in Settings              | ✅     |
| Single instance guard                    | ✅     |
| Port conflict detection                  | ✅     |
| Startup failure notification             | ✅     |
| Proxy URL copy button                    | ✅     |
| Windows installer (NSIS)                 | ✅     |
| Windows .exe / macOS .app / Linux binary | ✅     |
| LLMLingua / BERT compression             | V2     |
| Length-adaptive output quality gate      | V2     |
| Toast notifications (savings milestones) | V2     |
| Auto-update check                        | V2     |
| PyPI package                             | V2     |
| Docker image                             | V2     |

---

## License

MIT
