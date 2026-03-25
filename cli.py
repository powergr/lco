#!/usr/bin/env python3
"""
LCO — Unified CLI
==================
Single entry point for starting, controlling, and monitoring the LCO proxy.
No .env file required — every setting is a command-line flag with a sensible
default. Environment variables are also accepted (flag > env var > default).

Server commands
───────────────
  python3 cli.py start                        Start (foreground)
  python3 cli.py start --daemon               Start in background
  python3 cli.py start --openai-url http://localhost:11434   Use Ollama
  python3 cli.py start --mode medium --output-on             With compression
  python3 cli.py stop                         Stop daemon

Runtime control  (no restart needed)
─────────────────
  python3 cli.py status
  python3 cli.py mode aggressive
  python3 cli.py output on
  python3 cli.py memory on
  python3 cli.py memory window 6
  python3 cli.py gate threshold 0.40
  python3 cli.py embedder ollama
  python3 cli.py metrics
  python3 cli.py metrics --reset
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import sys
from pathlib import Path

import httpx
import typer

_HERE   = Path(__file__).resolve().parent
_PARENT = _HERE.parent
for _p in (_PARENT, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

app = typer.Typer(
    name="lco",
    help="LCO — LLM Context Optimizer",
    add_completion=False,
    no_args_is_help=True,
)

PID_FILE        = Path(".lco.pid")
VALID_MODES     = {"passthrough", "light", "medium", "aggressive"}
VALID_EMBEDDERS = {"tfidf", "ollama", "null"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _c(t: str, code: str) -> str:
    return f"\033[{code}m{t}\033[0m" if sys.stdout.isatty() else t

def green(t: str)  -> str: return _c(t, "32")
def yellow(t: str) -> str: return _c(t, "33")
def cyan(t: str)   -> str: return _c(t, "36")
def bold(t: str)   -> str: return _c(t, "1")
def dim(t: str)    -> str: return _c(t, "2")
def red(t: str)    -> str: return _c(t, "31")


def _pill(m: str | None) -> str:
    v = m or "passthrough"
    return {"passthrough": dim, "light": yellow, "medium": cyan,
            "aggressive": lambda t: _c(t, "31")}.get(v, dim)(v)


def _fmt(v: object, suffix: str = "", d: int = 1) -> str:
    if v is None: return dim("—")
    if isinstance(v, float): return f"{v:.{d}f}{suffix}"
    return f"{v}{suffix}"


def _banner() -> None:
    typer.echo(cyan(
        "\n  ╔══════════════════════════════╗\n"
        "  ║  LCO — LLM Context Optimizer ║\n"
        "  ╚══════════════════════════════╝\n"
    ))


def _url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _alive(host: str = "127.0.0.1", port: int = 8000) -> bool:
    try:
        return httpx.get(f"{_url(host, port)}/health", timeout=2).status_code == 200
    except Exception:
        return False


def _get(proxy: str, path: str) -> dict:
    try:
        r = httpx.get(f"{proxy}{path}", timeout=5)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        typer.echo(red(f"\n  Proxy not reachable at {proxy}"))
        typer.echo("  Start it:  python3 cli.py start\n")
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(red(f"  {e}"))
        raise typer.Exit(1)


def _post(proxy: str, path: str, data: dict) -> dict:
    try:
        r = httpx.post(f"{proxy}{path}", json=data, timeout=5)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        typer.echo(red(f"\n  Proxy not reachable at {proxy}"))
        typer.echo("  Start it:  python3 cli.py start\n")
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(red(f"  {e}"))
        raise typer.Exit(1)


def _run_server(host: str, port: int, log_level: str,
                env_overrides: dict[str, str]) -> None:
    for k, v in env_overrides.items():
        os.environ[k] = v
    import uvicorn
    from lco.main import app as fastapi_app
    uvicorn.run(fastapi_app, host=host, port=port, log_level=log_level.lower())


# ── start ─────────────────────────────────────────────────────────────────────

@app.command()
def start(
    host:      str  = typer.Option(os.getenv("LCO_HOST","127.0.0.1"), "--host"),
    port:      int  = typer.Option(int(os.getenv("LCO_PORT","8000")),  "--port","-p"),
    daemon:    bool = typer.Option(False, "--daemon","-d", help="Run in background"),
    log_level: str  = typer.Option(os.getenv("LCO_LOG_LEVEL","INFO"), "--log-level","-l"),

    openai_url:    str = typer.Option(os.getenv("LCO_OPENAI_BASE_URL",    "https://api.openai.com"),   "--openai-url",    help="OpenAI/Ollama upstream URL"),
    anthropic_url: str = typer.Option(os.getenv("LCO_ANTHROPIC_BASE_URL", "https://api.anthropic.com"),"--anthropic-url", help="Anthropic upstream URL"),

    mode:       str  = typer.Option(os.getenv("LCO_COMPRESSION_MODE","passthrough"), "--mode", help="passthrough|light|medium|aggressive"),
    output_on:  bool = typer.Option(os.getenv("LCO_OUTPUT_OPT","false").lower()=="true",  "--output-on/--no-output"),
    memory_on:  bool = typer.Option(os.getenv("LCO_MEMORY_COMPRESSION","false").lower()=="true", "--memory-on/--no-memory"),
    memory_win: int  = typer.Option(int(os.getenv("LCO_MEMORY_WINDOW","8")), "--memory-window"),
    gate_on:    bool = typer.Option(os.getenv("LCO_QUALITY_GATE","true").lower()=="true", "--gate-on/--no-gate"),
    threshold:  float= typer.Option(float(os.getenv("LCO_QUALITY_THRESHOLD","0.40")), "--threshold"),
    emb:        str  = typer.Option(os.getenv("LCO_EMBEDDER","tfidf"), "--embedder"),

    ollama_embed_model:    str = typer.Option(os.getenv("LCO_OLLAMA_EMBED_MODEL","nomic-embed-text"), "--ollama-embed-model"),
    ollama_compress_model: str = typer.Option(os.getenv("LCO_OLLAMA_COMPRESS_MODEL","qwen2.5:7b"),    "--ollama-compress-model"),
    llm_min_tokens:        int = typer.Option(int(os.getenv("LCO_LLM_COMPRESS_MIN_TOKENS","200")),    "--llm-min-tokens"),
    db_path:               str = typer.Option(os.getenv("LCO_DB_PATH","./lco_metrics.db"), "--db"),
) -> None:
    """Start the LCO proxy server."""
    _banner()

    if _alive(host, port):
        typer.echo(yellow(f"  Already running at {_url(host, port)}"))
        raise typer.Exit(0)

    env = {
        "LCO_HOST": host, "LCO_PORT": str(port), "LCO_LOG_LEVEL": log_level,
        "LCO_OPENAI_BASE_URL": openai_url, "LCO_ANTHROPIC_BASE_URL": anthropic_url,
        "LCO_COMPRESSION_MODE": mode,
        "LCO_OUTPUT_OPT":         "true" if output_on else "false",
        "LCO_MEMORY_COMPRESSION": "true" if memory_on else "false",
        "LCO_MEMORY_WINDOW":      str(memory_win),
        "LCO_QUALITY_GATE":       "true" if gate_on  else "false",
        "LCO_QUALITY_THRESHOLD":  str(threshold),
        "LCO_EMBEDDER":           emb,
        "LCO_OLLAMA_EMBED_MODEL":       ollama_embed_model,
        "LCO_OLLAMA_COMPRESS_MODEL":    ollama_compress_model,
        "LCO_LLM_COMPRESS_MIN_TOKENS":  str(llm_min_tokens),
        "LCO_DB_PATH":            db_path,
    }

    typer.echo(f"  {bold('Upstream')}:   OpenAI/Ollama → {openai_url}")
    typer.echo(f"               Anthropic    → {anthropic_url}")
    typer.echo(f"  {bold('Compression')}: {_pill(mode)}  output={'on' if output_on else 'off'}  memory={'on' if memory_on else 'off'}")
    typer.echo(f"  {bold('Gate')}:        {'on' if gate_on else 'off'}  threshold={threshold}  embedder={emb}\n")

    import time
    if daemon:
        proc = multiprocessing.Process(
            target=_run_server, args=(host, port, log_level, env), daemon=True,
        )
        proc.start()
        PID_FILE.write_text(str(proc.pid))
        for _ in range(20):
            time.sleep(0.25)
            if _alive(host, port): break
        if _alive(host, port):
            typer.echo(green(f"  Running  pid={proc.pid}  →  {_url(host, port)}/lco/dashboard"))
        else:
            typer.echo(red("  Did not start — check logs."))
            proc.terminate(); raise typer.Exit(1)
    else:
        typer.echo(dim(f"  Listening on {_url(host, port)}  (Ctrl+C to stop)\n"))
        try:
            _run_server(host, port, log_level, env)
        except KeyboardInterrupt:
            typer.echo("\n  Stopped.")


# ── stop ──────────────────────────────────────────────────────────────────────

@app.command()
def stop() -> None:
    """Stop a daemonised proxy."""
    if not PID_FILE.exists():
        typer.echo(yellow("  No PID file found.")); raise typer.Exit(1)
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        typer.echo(green(f"  Stopped (pid={pid})"))
    except ProcessLookupError:
        typer.echo(yellow("  Process not found — removing PID file."))
        PID_FILE.unlink(missing_ok=True)


# ── status ────────────────────────────────────────────────────────────────────

@app.command()
def status(proxy: str = typer.Option("http://127.0.0.1:8000","--proxy","-P")) -> None:
    """Show live config and metrics."""
    data = _get(proxy, "/lco/status")
    m = data.get("metrics") or {}
    _banner()
    typer.echo(f"  {bold('Compression')}")
    typer.echo(f"    Mode        {_pill(data.get('compression_mode'))}")
    typer.echo(f"    Output opt  {'on' if data.get('output_optimization') else dim('off')}")
    typer.echo(f"    Memory      {'on' if data.get('memory_compression') else dim('off')}  window={data.get('memory_window','—')}")
    typer.echo(f"    Gate        {'on' if data.get('quality_gate_enabled') else dim('off')}  threshold={data.get('quality_threshold','—')}  embedder={data.get('embedder','tfidf')}")
    typer.echo(f"\n  {bold('Traffic')}  (last 1000 requests)")
    typer.echo(f"    Requests    {_fmt(m.get('total_requests'))}")
    typer.echo(f"    In saved    {green(_fmt(m.get('total_input_saved')))} tok")
    typer.echo(f"    Out saved   {green(_fmt(m.get('total_output_saved')))} tok")
    typer.echo(f"    Total tok   {_fmt(m.get('total_input_tokens'))} in / {_fmt(m.get('total_output_tokens'))} out")
    typer.echo(f"    Avg latency {_fmt(m.get('avg_latency_ms'), ' ms')}")
    typer.echo(f"    Quality     {_fmt(m.get('avg_quality_score'), d=2)}\n")


# ── mode ──────────────────────────────────────────────────────────────────────

@app.command()
def mode(
    value: str = typer.Argument(..., help="passthrough|light|medium|aggressive"),
    proxy: str = typer.Option("http://127.0.0.1:8000","--proxy","-P"),
) -> None:
    """Set compression mode at runtime."""
    if value not in VALID_MODES:
        typer.echo(red(f"  Invalid '{value}'. Valid: {', '.join(sorted(VALID_MODES))}")); raise typer.Exit(1)
    _post(proxy, "/lco/control", {"compression_mode": value})
    typer.echo(green(f"  ✓ Mode → {_pill(value)}"))


# ── output ────────────────────────────────────────────────────────────────────

@app.command()
def output(
    state: str = typer.Argument(..., help="on | off"),
    proxy: str = typer.Option("http://127.0.0.1:8000","--proxy","-P"),
) -> None:
    """Toggle output compression."""
    on = state.lower() in ("on","true","1")
    _post(proxy, "/lco/control", {"output_optimization": on})
    typer.echo(green(f"  ✓ Output → {'on' if on else 'off'}"))


# ── memory ────────────────────────────────────────────────────────────────────

@app.command()
def memory(
    args:  list[str] = typer.Argument(..., help="on|off  OR  window N"),
    proxy: str       = typer.Option("http://127.0.0.1:8000","--proxy","-P"),
) -> None:
    """Toggle memory compression or set window size."""
    if not args: typer.echo(red("  memory on|off   OR   memory window N")); raise typer.Exit(1)
    if args[0].lower() in ("on","off"):
        on = args[0].lower() == "on"
        _post(proxy, "/lco/control", {"memory_compression": on})
        typer.echo(green(f"  ✓ Memory → {'on' if on else 'off'}"))
    elif args[0] == "window" and len(args) > 1:
        n = int(args[1])
        _post(proxy, "/lco/control", {"memory_window": n})
        typer.echo(green(f"  ✓ Window → {n} turns"))
    else:
        typer.echo(red(f"  Unknown: {' '.join(args)}")); raise typer.Exit(1)


# ── gate ──────────────────────────────────────────────────────────────────────

@app.command()
def gate(
    args:  list[str] = typer.Argument(..., help="on|off  OR  threshold 0.40"),
    proxy: str       = typer.Option("http://127.0.0.1:8000","--proxy","-P"),
) -> None:
    """Toggle quality gate or set threshold."""
    if not args: typer.echo(red("  gate on|off   OR   gate threshold 0.40")); raise typer.Exit(1)
    if args[0].lower() in ("on","off"):
        on = args[0].lower() == "on"
        _post(proxy, "/lco/control", {"quality_gate_enabled": on})
        typer.echo(green(f"  ✓ Gate → {'on' if on else 'off'}"))
    elif args[0] == "threshold" and len(args) > 1:
        t = float(args[1])
        _post(proxy, "/lco/control", {"quality_threshold": t})
        typer.echo(green(f"  ✓ Threshold → {t}"))
    else:
        typer.echo(red(f"  Unknown: {' '.join(args)}")); raise typer.Exit(1)


# ── embedder ──────────────────────────────────────────────────────────────────

@app.command()
def embedder(
    value: str = typer.Argument(..., help="tfidf | ollama | null"),
    proxy: str = typer.Option("http://127.0.0.1:8000","--proxy","-P"),
) -> None:
    """Switch quality gate embedder."""
    if value not in VALID_EMBEDDERS:
        typer.echo(red(f"  Invalid '{value}'. Valid: {', '.join(VALID_EMBEDDERS)}")); raise typer.Exit(1)
    _post(proxy, "/lco/control", {"embedder": value})
    typer.echo(green(f"  ✓ Embedder → {value}"))
    if value == "ollama":
        _post(proxy, "/lco/control", {"quality_threshold": 0.80})
        typer.echo(dim("    Threshold auto-set to 0.80 for neural embeddings"))


# ── metrics ───────────────────────────────────────────────────────────────────

@app.command()
def metrics(
    reset: bool = typer.Option(False,"--reset", help="Clear all metrics"),
    proxy: str  = typer.Option("http://127.0.0.1:8000","--proxy","-P"),
) -> None:
    """Show recent requests or clear metrics."""
    if reset:
        _post(proxy, "/lco/control", {"reset_metrics": True})
        typer.echo(green("  ✓ Metrics cleared")); return
    rows = _get(proxy, "/lco/recent")
    if not rows: typer.echo(dim("\n  No requests yet.\n")); return
    typer.echo(f"\n  {bold('Recent requests')}\n")
    W = [22,10,7,7,7,7,8,9]
    H = ["Model","Mode","In","In✓","Out","Out✓","Quality","Latency"]
    sep = "  "+"  ".join("─"*w for w in W)
    typer.echo("  "+"  ".join(h.ljust(W[i]) for i,h in enumerate(H)))
    typer.echo(sep)
    for r in rows[:15]:
        model = (r.get("model") or "—")[:W[0]-1]
        in_s  = r.get("input_tokens_saved")  or 0
        out_s = r.get("output_tokens_saved") or 0
        row = [model, _pill(r.get("compression_mode")),
               str(r.get("input_tokens") or "—"),
               green(str(in_s)) if in_s>0 else dim("—"),
               str(r.get("output_tokens") or "—"),
               green(str(out_s)) if out_s>0 else dim("—"),
               f"{r['quality_score']:.2f}" if r.get("quality_score") else dim("—"),
               _fmt(r.get("latency_ms"),"ms",0)]
        typer.echo("  "+"  ".join(str(v).ljust(W[i]) for i,v in enumerate(row)))
    typer.echo(sep+"\n")


def main() -> None:
    app()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()