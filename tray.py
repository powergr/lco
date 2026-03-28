#!/usr/bin/env python3
"""
LCO — System Tray / Menu Bar App  v0.2.0
==========================================
Autonomous tray app — no command-line arguments needed.
All settings are persisted to:
  Windows : %APPDATA%\\LCO\\settings.json
  macOS   : ~/Library/Application Support/LCO/settings.json
  Linux   : ~/.local/share/LCO/settings.json

Data files (DB, log) go in the same folder.

Usage (for developers):
  python3 tray.py          # uses stored settings or opens first-run wizard

Dependencies:
  pip install pystray Pillow
  Linux: sudo apt install python3-tk libappindicator3-1
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

# ── Freeze-aware path setup ───────────────────────────────────────────────────
import sys as _sys
if getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
    _BUNDLE = Path(_sys._MEIPASS)
    if str(_BUNDLE) not in _sys.path:
        _sys.path.insert(0, str(_BUNDLE))
else:
    _HERE   = Path(__file__).resolve().parent
    _PARENT = _HERE.parent
    for _p in (_PARENT, _HERE):
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))

from lco.version import __version__ as VERSION

# ── App data directory (platform-aware) ──────────────────────────────────────

def _app_data_dir() -> Path:
    """Return the per-user app data directory for LCO, creating it if needed."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "LCO"
    d.mkdir(parents=True, exist_ok=True)
    return d

APP_DIR = _app_data_dir()
SETTINGS_FILE = APP_DIR / "settings.json"
DB_PATH       = APP_DIR / "lco_metrics.db"
LOG_PATH      = APP_DIR / "lco.log"

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict[str, Any] = {
    "provider":           "openai",
    "openai_url":         "https://api.openai.com",
    "anthropic_url":      "https://api.anthropic.com",
    "api_key":            "",
    "anthropic_api_key":  "",
    "model":              "gpt-4o-mini",
    "mode":               "light",
    "output_on":          False,
    "memory_on":          False,
    "memory_window":      8,
    "threshold":          0.40,
    "embedder":           "tfidf",
    "port":               8000,
    "host":               "127.0.0.1",
    "first_run":          True,
}

PROVIDER_URLS = {
    "openai":     "https://api.openai.com",
    "openrouter": "https://openrouter.ai/api",
    "ollama":     "http://localhost:11434",
    "groq":       "https://api.groq.com/openai",
    "mistral":    "https://api.mistral.ai",
    "together":   "https://api.together.xyz",
    "deepseek":   "https://api.deepseek.com",
    "anthropic":  "https://api.anthropic.com",
}

PROVIDER_MODELS = {
    "openai":     ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo", "o1-mini"],
    "openrouter": ["openai/gpt-4o-mini", "anthropic/claude-3-5-sonnet",
                   "meta-llama/llama-3.1-8b-instruct:free",
                   "nvidia/llama-3.1-nemotron-70b-instruct:free"],
    "ollama":     ["llama3.2", "qwen2.5:7b", "mistral", "phi3"],
    "groq":       ["llama-3.3-70b-versatile", "mixtral-8x7b-32768",
                   "llama-3.1-8b-instant"],
    "mistral":    ["mistral-large-latest", "mistral-small-latest",
                   "codestral-latest"],
    "together":   ["meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
                   "mistralai/Mixtral-8x7B-Instruct-v0.1"],
    "deepseek":   ["deepseek-chat", "deepseek-coder"],
    "anthropic":  ["claude-opus-4-5", "claude-sonnet-4-6", "claude-haiku-4-5"],
}

PROVIDERS_NO_KEY = {"ollama"}   # these don't need an API key
INPUT_PRICE_PER_M  = 2.50
OUTPUT_PRICE_PER_M = 10.00
MODES = ["passthrough", "light", "medium", "aggressive"]
POLL_INTERVAL = 3
ICON_SIZE     = 64


# ── Settings persistence ──────────────────────────────────────────────────────

def load_settings() -> dict[str, Any]:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def save_settings(s: dict[str, Any]) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[LCO] Could not save settings: {e}")


# ── Icon generation ───────────────────────────────────────────────────────────

def _make_icon(running: bool, mode: str) -> "PILImage":
    from PIL import Image, ImageDraw
    canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)
    ring = (60, 200, 100, 255) if running else (120, 120, 120, 255)
    fill = (40, 170,  80, 255) if running else ( 90,  90,  90, 255)
    draw.ellipse([2, 2, ICON_SIZE-2, ICON_SIZE-2], outline=ring, width=4)
    draw.ellipse([8, 8, ICON_SIZE-8, ICON_SIZE-8], fill=fill)
    w = (255, 255, 255, 255)
    cx, cy = ICON_SIZE // 2, ICON_SIZE // 2
    draw.rectangle([cx-8, cy-14, cx-2, cy+12], fill=w)
    draw.rectangle([cx-8, cy+6,  cx+10, cy+12], fill=w)
    dot_col = {"passthrough":(150,150,150,255),"light":(255,200,50,255),
               "medium":(100,140,255,255),"aggressive":(255,80,80,255)
               }.get(mode,(150,150,150,255))
    if running:
        r = 9
        draw.ellipse([ICON_SIZE-r*2-1,ICON_SIZE-r*2-1,ICON_SIZE-1,ICON_SIZE-1],
                     fill=dot_col)
    bg = Image.new("RGB", (ICON_SIZE, ICON_SIZE), (30, 30, 30))
    bg.paste(canvas, mask=canvas.split()[3])
    return bg


# ── Cost helpers ──────────────────────────────────────────────────────────────

def _to_dollars(in_saved: int, out_saved: int) -> float:
    return in_saved/1_000_000*INPUT_PRICE_PER_M + out_saved/1_000_000*OUTPUT_PRICE_PER_M

def _fmt_dollars(v: float) -> str:
    return f"${v:.4f}" if v >= 0.01 else f"${v*100:.3f}¢"


# ── Proxy client ──────────────────────────────────────────────────────────────

class ProxyClient:
    def __init__(self, host: str, port: int):
        self.base = f"http://{host}:{port}"
        self._c   = httpx.Client(timeout=3)

    def is_alive(self) -> bool:
        try: return self._c.get(f"{self.base}/health").status_code == 200
        except Exception: return False

    def status(self) -> dict[str, Any]:
        try: return self._c.get(f"{self.base}/lco/status").json()
        except Exception: return {}

    def set(self, **kw: Any) -> None:
        try: self._c.post(f"{self.base}/lco/control", json=kw)
        except Exception: pass

    def dashboard_url(self) -> str: return f"{self.base}/lco/dashboard"
    def close(self) -> None: self._c.close()


# ── Shared state ──────────────────────────────────────────────────────────────

class AppState:
    def __init__(self, settings: dict[str, Any]):
        self.lock   = threading.Lock()
        self.running = False
        self.mode    = settings["mode"]
        self.output_on = settings["output_on"]
        self.total_in_saved  = 0
        self.total_out_saved = 0
        self.total_requests  = 0
        self.session_dollars = 0.0
        self.total_dollars   = 0.0
        self._base_in  = -1
        self._base_out = -1

    def update(self, data: dict[str, Any]) -> None:
        m = data.get("metrics") or {}
        in_s  = int(m.get("total_input_saved")  or 0)
        out_s = int(m.get("total_output_saved") or 0)
        with self.lock:
            self.running = True
            self.mode      = data.get("compression_mode", self.mode)
            self.output_on = bool(data.get("output_optimization"))
            if self._base_in < 0:
                self._base_in, self._base_out = in_s, out_s
            self.total_in_saved  = in_s
            self.total_out_saved = out_s
            self.total_requests  = int(m.get("total_requests") or 0)
            self.session_dollars = _to_dollars(max(0,in_s-self._base_in),
                                               max(0,out_s-self._base_out))
            self.total_dollars   = _to_dollars(in_s, out_s)

    def mark_stopped(self) -> None:
        with self.lock: self.running = False


# ── Server thread ─────────────────────────────────────────────────────────────

def _server_thread(env: dict[str, str]) -> None:
    for k, v in env.items():
        os.environ[k] = v
    try:
        import uvicorn
        from lco.main import app as fastapi_app
        uvicorn.run(fastapi_app, host=env["LCO_HOST"],
                    port=int(env["LCO_PORT"]), log_level="warning")
    except Exception as exc:
        print(f"[LCO] Server error: {exc}")


def _poll_loop(client: ProxyClient, state: AppState,
               icon_ref: list, stop: threading.Event) -> None:
    while not stop.is_set():
        alive = client.is_alive()
        if alive:
            data = client.status()
            if data: state.update(data)
        else:
            state.mark_stopped()
        if icon_ref:
            try: icon_ref[0].icon = _make_icon(state.running, state.mode)
            except Exception: pass
        stop.wait(POLL_INTERVAL)


# ── First-run / Settings window ───────────────────────────────────────────────

def _show_settings(root: Any, settings: dict[str, Any],
                   client: ProxyClient, icon_ref: list,
                   state: AppState, stop: threading.Event,
                   on_save: Any | None = None) -> None:
    """
    Full settings dialog — provider, API key, model, compression, proxy port.
    Called on first run automatically, or via tray menu → Settings.
    on_save: optional callback(new_settings) called after user saves.
    """
    import tkinter as tk
    from tkinter import font as tkfont, messagebox

    win = tk.Toplevel(root)
    win.title("LCO — Settings")
    win.resizable(False, False)
    win.attributes("-topmost", True)

    BG = "#1a1d27"; SURFACE = "#21253a"; TEXT = "#e2e8f0"
    MUTED = "#8892a4"; GREEN = "#4ade80"; ACCENT = "#6c8aff"
    AMBER = "#fbbf24"; RED_C = "#f87171"

    win.configure(bg=BG)
    label_f = tkfont.Font(family="Segoe UI", size=9)
    bold_f  = tkfont.Font(family="Segoe UI", size=10, weight="bold")
    head_f  = tkfont.Font(family="Segoe UI", size=12, weight="bold")
    mono_f  = tkfont.Font(family="Consolas", size=9)

    def _lbl(parent: Any, text: str, **kw: Any) -> tk.Label:
        return tk.Label(parent, text=text, font=label_f,
                        fg=MUTED, bg=kw.pop("bg", BG), **kw)

    def _section(text: str) -> tk.Frame:
        f = tk.Frame(win, bg=BG)
        f.pack(fill="x", padx=16, pady=(10, 2))
        tk.Label(f, text=text, font=bold_f, fg=ACCENT, bg=BG).pack(anchor="w")
        tk.Frame(win, bg=SURFACE, height=1).pack(fill="x", padx=16, pady=(0, 6))
        return f

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(win, bg=SURFACE, pady=12)
    hdr.pack(fill="x")
    tk.Label(hdr, text="⚡ LCO Settings", font=head_f,
             fg=ACCENT, bg=SURFACE).pack(side="left", padx=16)
    tk.Label(hdr, text=f"v{VERSION}", font=label_f,
             fg=MUTED, bg=SURFACE).pack(side="right", padx=16)

    # ── Provider & API key ────────────────────────────────────────────────────
    _section("Provider & API Key")

    prov_f = tk.Frame(win, bg=BG)
    prov_f.pack(fill="x", padx=16, pady=2)
    _lbl(prov_f, "Provider").grid(row=0, column=0, sticky="w", padx=(0,12))

    provider_var = tk.StringVar(value=settings.get("provider", "openai"))
    providers    = list(PROVIDER_URLS.keys())

    prov_menu = tk.OptionMenu(prov_f, provider_var, *providers)
    prov_menu.config(font=label_f, bg=SURFACE, fg=TEXT,
                     activebackground=ACCENT, relief="flat",
                     highlightthickness=0, width=14)
    prov_menu.grid(row=0, column=1, sticky="w")

    # Custom URL override
    url_f = tk.Frame(win, bg=BG)
    url_f.pack(fill="x", padx=16, pady=2)
    _lbl(url_f, "Upstream URL").grid(row=0, column=0, sticky="w", padx=(0,12))
    url_var = tk.StringVar(value=settings.get("openai_url", "https://api.openai.com"))
    url_entry = tk.Entry(url_f, textvariable=url_var, font=mono_f,
                         bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                         relief="flat", width=36)
    url_entry.grid(row=0, column=1, sticky="w", pady=2)

    # API key field
    key_f = tk.Frame(win, bg=BG)
    key_f.pack(fill="x", padx=16, pady=2)
    _lbl(key_f, "API Key").grid(row=0, column=0, sticky="w", padx=(0,12))
    key_var = tk.StringVar(value=settings.get("api_key", ""))
    key_entry = tk.Entry(key_f, textvariable=key_var, font=mono_f,
                         show="•", bg=SURFACE, fg=TEXT,
                         insertbackground=TEXT, relief="flat", width=36)
    key_entry.grid(row=0, column=1, sticky="w", pady=2)
    show_key_var = tk.BooleanVar(value=False)
    def _toggle_key_vis() -> None:
        key_entry.config(show="" if show_key_var.get() else "•")
    tk.Checkbutton(key_f, text="Show", variable=show_key_var,
                   command=_toggle_key_vis,
                   font=label_f, bg=BG, fg=MUTED,
                   activebackground=BG, selectcolor=SURFACE).grid(row=0, column=2, padx=6)

    # Auto-fill URL when provider changes
    def _on_provider_change(*_: Any) -> None:
        p = provider_var.get()
        url_var.set(PROVIDER_URLS.get(p, "https://api.openai.com"))
        if p in PROVIDERS_NO_KEY:
            key_entry.config(state="disabled")
            key_var.set("no key required")
        else:
            key_entry.config(state="normal")
            if key_var.get() == "no key required":
                key_var.set("")
        # Refresh model dropdown
        _refresh_models()

    provider_var.trace_add("write", _on_provider_change)

    # ── Model ─────────────────────────────────────────────────────────────────
    _section("Model")
    mod_f = tk.Frame(win, bg=BG)
    mod_f.pack(fill="x", padx=16, pady=2)
    _lbl(mod_f, "Model").grid(row=0, column=0, sticky="w", padx=(0,12))

    model_var = tk.StringVar(value=settings.get("model", "gpt-4o-mini"))
    model_menu = tk.OptionMenu(mod_f, model_var, model_var.get())
    model_menu.config(font=label_f, bg=SURFACE, fg=TEXT,
                      activebackground=ACCENT, relief="flat",
                      highlightthickness=0, width=30)
    model_menu.grid(row=0, column=1, sticky="w")

    # Custom model entry
    custom_f = tk.Frame(win, bg=BG)
    custom_f.pack(fill="x", padx=16, pady=2)
    _lbl(custom_f, "Or type model").grid(row=0, column=0, sticky="w", padx=(0,12))
    custom_var = tk.StringVar(value="")
    tk.Entry(custom_f, textvariable=custom_var, font=mono_f,
             bg=SURFACE, fg=TEXT, insertbackground=TEXT,
             relief="flat", width=36).grid(row=0, column=1, sticky="w")
    _lbl(custom_f, "(overrides dropdown)", bg=BG).grid(row=0, column=2, padx=6)

    def _refresh_models() -> None:
        p = provider_var.get()
        models = PROVIDER_MODELS.get(p, [model_var.get()])
        menu = model_menu["menu"]
        menu.delete(0, "end")
        for m in models:
            menu.add_command(label=m, command=lambda _m=m: model_var.set(_m))
        if model_var.get() not in models:
            model_var.set(models[0])

    _refresh_models()

    # ── Compression ───────────────────────────────────────────────────────────
    _section("Compression")
    comp_f = tk.Frame(win, bg=BG)
    comp_f.pack(fill="x", padx=16, pady=4)

    _lbl(comp_f, "Mode").grid(row=0, column=0, sticky="w", padx=(0,12), pady=2)
    mode_var = tk.StringVar(value=settings.get("mode", "light"))
    MODE_COLS = {"passthrough": MUTED, "light": AMBER,
                 "medium": ACCENT, "aggressive": RED_C}
    mode_btns: dict[str, tk.Button] = {}
    btn_row = tk.Frame(comp_f, bg=BG)
    btn_row.grid(row=0, column=1, sticky="w")
    for _m in MODES:
        b = tk.Button(btn_row, text=_m.capitalize(), font=label_f,
                      relief="flat", bd=0, padx=8, pady=3, cursor="hand2",
                      command=lambda __m=_m: _pick_mode(__m))
        b.pack(side="left", padx=2)
        mode_btns[_m] = b

    def _pick_mode(m: str) -> None:
        mode_var.set(m)
        for _m2, btn in mode_btns.items():
            active = _m2 == m
            col    = MODE_COLS.get(_m2, MUTED)
            btn.configure(bg=col if active else SURFACE,
                          fg="#000" if active else MUTED)

    _pick_mode(mode_var.get())

    out_var = tk.BooleanVar(value=settings.get("output_on", False))
    mem_var = tk.BooleanVar(value=settings.get("memory_on", False))
    _lbl(comp_f, "Output compression").grid(row=1, column=0, sticky="w", padx=(0,12), pady=2)
    tk.Checkbutton(comp_f, variable=out_var, bg=BG, fg=TEXT,
                   activebackground=BG, selectcolor=SURFACE,
                   font=label_f).grid(row=1, column=1, sticky="w")
    _lbl(comp_f, "Memory compression").grid(row=2, column=0, sticky="w", padx=(0,12), pady=2)
    tk.Checkbutton(comp_f, variable=mem_var, bg=BG, fg=TEXT,
                   activebackground=BG, selectcolor=SURFACE,
                   font=label_f).grid(row=2, column=1, sticky="w")

    # ── Proxy port ────────────────────────────────────────────────────────────
    _section("Proxy")
    port_f = tk.Frame(win, bg=BG)
    port_f.pack(fill="x", padx=16, pady=2)
    _lbl(port_f, "Listen port").grid(row=0, column=0, sticky="w", padx=(0,12))
    port_var = tk.StringVar(value=str(settings.get("port", 8000)))
    tk.Entry(port_f, textvariable=port_var, font=mono_f,
             bg=SURFACE, fg=TEXT, insertbackground=TEXT,
             relief="flat", width=8).grid(row=0, column=1, sticky="w")
    _lbl(port_f,
         "Your apps connect to:  http://127.0.0.1:{port}/v1",
         bg=BG).grid(row=0, column=2, sticky="w", padx=8)

    # ── Save / Cancel ─────────────────────────────────────────────────────────
    act_f = tk.Frame(win, bg=SURFACE, pady=10)
    act_f.pack(fill="x", pady=(12, 0))

    status_lbl = tk.Label(act_f, text="", font=label_f, fg=GREEN, bg=SURFACE)
    status_lbl.pack(side="left", padx=16)

    def _save() -> None:
        model_final = custom_var.get().strip() or model_var.get()
        p = provider_var.get()
        # Warn if API key missing for providers that need one
        if p not in PROVIDERS_NO_KEY and not key_var.get().strip():
            messagebox.showwarning("API Key Missing",
                f"An API key is required for {p}.\nRequests will fail without one.",
                parent=win)
        new_s = dict(settings)
        new_s.update({
            "provider":    p,
            "openai_url":  url_var.get().strip(),
            "api_key":     key_var.get().strip(),
            "model":       model_final,
            "mode":        mode_var.get(),
            "output_on":   out_var.get(),
            "memory_on":   mem_var.get(),
            "port":        int(port_var.get() or 8000),
            "first_run":   False,
        })
        save_settings(new_s)
        settings.update(new_s)
        # Apply runtime-changeable settings immediately (no restart needed)
        client.set(compression_mode=new_s["mode"],
                   output_optimization=new_s["output_on"],
                   memory_compression=new_s["memory_on"])
        state.mode      = new_s["mode"]
        state.output_on = new_s["output_on"]
        if icon_ref:
            icon_ref[0].menu = _build_menu(state, client, icon_ref,
                                           win.winfo_toplevel(), settings, stop)
        status_lbl.config(text="✓ Saved")
        win.after(1500, win.destroy)
        if on_save:
            on_save(new_s)

    tk.Button(act_f, text="Save & Apply", font=label_f,
              bg=GREEN, fg="#000", relief="flat", padx=16, pady=6,
              cursor="hand2", command=_save).pack(side="right", padx=8)
    tk.Button(act_f, text="Cancel", font=label_f,
              bg=SURFACE, fg=MUTED, relief="flat", padx=12, pady=6,
              cursor="hand2", command=win.destroy).pack(side="right")

    # Centre on screen
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h   = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")


# ── Status popup ──────────────────────────────────────────────────────────────

def _show_status(root: Any, state: AppState, client: ProxyClient,
                 icon_ref: list, settings: dict[str, Any],
                 stop: threading.Event) -> None:
    import tkinter as tk
    from tkinter import font as tkfont

    popup = tk.Toplevel(root)
    popup.title("LCO — Status")
    popup.resizable(False, False)
    popup.attributes("-topmost", True)

    BG = "#1a1d27"; SURFACE = "#21253a"; TEXT = "#e2e8f0"
    MUTED = "#8892a4"; GREEN = "#4ade80"; ACCENT = "#6c8aff"
    AMBER = "#fbbf24"; RED_C = "#f87171"
    MC = {"passthrough":MUTED,"light":AMBER,"medium":ACCENT,"aggressive":RED_C}

    popup.configure(bg=BG)
    bold_f  = tkfont.Font(family="Segoe UI", size=11, weight="bold")
    label_f = tkfont.Font(family="Segoe UI", size=9)
    big_f   = tkfont.Font(family="Segoe UI", size=18, weight="bold")
    mid_f   = tkfont.Font(family="Segoe UI", size=13, weight="bold")
    mono_f  = tkfont.Font(family="Consolas",  size=10)

    hdr = tk.Frame(popup, bg=SURFACE, pady=10)
    hdr.pack(fill="x")
    tk.Label(hdr, text="⚡ LCO", font=bold_f, fg=ACCENT, bg=SURFACE).pack(side="left",  padx=16)
    dot = tk.Label(hdr, text="●", font=label_f, bg=SURFACE)
    dot.pack(side="left")
    tk.Label(hdr, text=f"v{VERSION}", font=label_f, fg=MUTED, bg=SURFACE).pack(side="right", padx=16)

    sf = tk.Frame(popup, bg=BG, pady=8)
    sf.pack(fill="x", padx=16, pady=(12, 4))
    tk.Label(sf, text="💰 Saved this session", font=label_f, fg=MUTED, bg=BG).pack(anchor="w")
    sess_lbl = tk.Label(sf, text="$0.0000", font=big_f, fg=GREEN, bg=BG)
    sess_lbl.pack(anchor="w")
    tk.Label(sf, text="All-time savings", font=label_f, fg=MUTED, bg=BG).pack(anchor="w", pady=(8,0))
    all_lbl  = tk.Label(sf, text="$0.0000", font=mid_f, fg=GREEN, bg=BG)
    all_lbl.pack(anchor="w")

    tf = tk.Frame(popup, bg=SURFACE, padx=16, pady=8)
    tf.pack(fill="x", pady=4)
    in_lbl  = tk.Label(tf, text="Input saved:  —", font=mono_f, fg=TEXT,  bg=SURFACE)
    out_lbl = tk.Label(tf, text="Output saved: —", font=mono_f, fg=TEXT,  bg=SURFACE)
    req_lbl = tk.Label(tf, text="Requests:     —", font=mono_f, fg=MUTED, bg=SURFACE)
    in_lbl.pack(anchor="w"); out_lbl.pack(anchor="w"); req_lbl.pack(anchor="w")

    # Mode selector (quick access)
    mf = tk.Frame(popup, bg=BG, pady=6)
    mf.pack(fill="x", padx=16, pady=4)
    tk.Label(mf, text="Mode", font=label_f, fg=MUTED, bg=BG).pack(anchor="w", pady=(0,4))
    br = tk.Frame(mf, bg=BG); br.pack(fill="x")
    mode_btns: dict[str, tk.Button] = {}
    for m in MODES:
        b = tk.Button(br, text=m.capitalize(), font=label_f, relief="flat",
                      bd=0, padx=8, pady=4, cursor="hand2",
                      command=lambda _m=m: _set_mode(_m))
        b.pack(side="left", padx=2); mode_btns[m] = b

    of = tk.Frame(popup, bg=BG); of.pack(fill="x", padx=16, pady=2)
    tk.Label(of, text="Output compression", font=label_f, fg=MUTED, bg=BG).pack(side="left")
    out_btn = tk.Button(of, text="OFF", font=label_f, relief="flat",
                        bd=0, padx=8, pady=3, cursor="hand2",
                        command=lambda: _toggle_out())
    out_btn.pack(side="right")

    af = tk.Frame(popup, bg=SURFACE, pady=8); af.pack(fill="x", pady=(8,0))
    tk.Button(af, text="📊 Dashboard", font=label_f, relief="flat", bd=0,
              fg=ACCENT, bg=SURFACE, padx=12, pady=6, cursor="hand2",
              command=lambda: webbrowser.open(client.dashboard_url())).pack(side="left", padx=8)
    tk.Button(af, text="⚙ Settings", font=label_f, relief="flat", bd=0,
              fg=MUTED, bg=SURFACE, padx=12, pady=6, cursor="hand2",
              command=lambda: (popup.destroy(),
                               root.after(0, lambda: _show_settings(
                                   root, settings, client, icon_ref,
                                   state, stop)))).pack(side="left", padx=4)
    tk.Button(af, text="✕", font=label_f, relief="flat", bd=0,
              fg=MUTED, bg=SURFACE, padx=12, pady=6, cursor="hand2",
              command=popup.destroy).pack(side="right", padx=8)

    def _upd_mode_btns(cur: str) -> None:
        for _m, btn in mode_btns.items():
            a = _m == cur
            btn.configure(bg=MC.get(_m,MUTED) if a else SURFACE,
                          fg="#000" if a else MUTED)

    def _set_mode(m: str) -> None:
        client.set(compression_mode=m); state.mode = m; _upd_mode_btns(m)
        if icon_ref:
            icon_ref[0].menu = _build_menu(state,client,icon_ref,root,settings,stop)

    def _toggle_out() -> None:
        new = not state.output_on; client.set(output_optimization=new)
        state.output_on = new
        out_btn.configure(text="ON ✓" if new else "OFF",
                          bg=GREEN if new else SURFACE,
                          fg="#000" if new else MUTED)

    def _tick() -> None:
        with state.lock:
            running=state.running; sess=state.session_dollars
            total=state.total_dollars; in_s=state.total_in_saved
            out_s=state.total_out_saved; reqs=state.total_requests; cur=state.mode
        dot.configure(fg=GREEN if running else RED_C)
        sess_lbl.configure(text=_fmt_dollars(sess))
        all_lbl.configure(text=_fmt_dollars(total))
        in_lbl.configure(text=f"Input saved:  {in_s:,} tokens")
        out_lbl.configure(text=f"Output saved: {out_s:,} tokens")
        req_lbl.configure(text=f"Requests:     {reqs:,}")
        _upd_mode_btns(cur)
        out_btn.configure(text="ON ✓" if state.output_on else "OFF",
                          bg=GREEN if state.output_on else SURFACE,
                          fg="#000" if state.output_on else MUTED)
        if popup.winfo_exists(): popup.after(2000, _tick)

    _upd_mode_btns(state.mode)
    out_btn.configure(text="ON ✓" if state.output_on else "OFF",
                      bg=GREEN if state.output_on else SURFACE,
                      fg="#000" if state.output_on else MUTED)
    _tick()
    popup.update_idletasks()
    sw=popup.winfo_screenwidth(); sh=popup.winfo_screenheight()
    w=popup.winfo_reqwidth(); h=popup.winfo_reqheight()
    popup.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")


# ── Tray menu ─────────────────────────────────────────────────────────────────

def _build_menu(state: AppState, client: ProxyClient, icon_ref: list,
                root: Any, settings: dict[str, Any],
                stop: threading.Event) -> Any:
    import pystray

    def _mode_item(m: str) -> Any:
        chk = "✓ " if state.mode == m else "  "
        desc= {"passthrough":"Off","light":"Light","medium":"Medium","aggressive":"Max"}[m]
        return pystray.MenuItem(f"{chk}{desc}", lambda: _switch(m))

    def _switch(m: str) -> None:
        client.set(compression_mode=m); state.mode = m
        settings["mode"] = m; save_settings(settings)
        if icon_ref:
            icon_ref[0].menu = _build_menu(state,client,icon_ref,root,settings,stop)

    def _toggle_out() -> None:
        new = not state.output_on; client.set(output_optimization=new)
        state.output_on = new; settings["output_on"] = new; save_settings(settings)
        if icon_ref:
            icon_ref[0].menu = _build_menu(state,client,icon_ref,root,settings,stop)

    def _open_status() -> None:
        root.after(0, lambda: _show_status(root,state,client,icon_ref,settings,stop))

    def _open_settings() -> None:
        root.after(0, lambda: _show_settings(root,settings,client,icon_ref,state,stop))

    def _quit() -> None:
        if icon_ref: icon_ref[0].stop()
        root.after(0, root.quit)

    out_lbl = f"{'✓' if state.output_on else '  '} Output compression"

    # Show savings in menu title
    with state.lock:
        sess = state.session_dollars
    saved_str = f"💰 {_fmt_dollars(sess)} saved this session"

    return pystray.Menu(
        pystray.MenuItem("⚡ LCO", None, enabled=False),
        pystray.MenuItem(saved_str, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Mode", pystray.Menu(*[_mode_item(m) for m in MODES])),
        pystray.MenuItem(out_lbl, _toggle_out),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("📊 Status & Savings...", _open_status),
        pystray.MenuItem("⚙ Settings...", _open_settings),
        pystray.MenuItem("🌐 Open Dashboard", lambda: webbrowser.open(client.dashboard_url())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit LCO", _quit),
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        import pystray
    except ImportError:
        print("pystray not installed. Run:  pip install pystray Pillow")
        sys.exit(1)
    try:
        import tkinter as tk
    except ImportError:
        print("tkinter not available. Linux: sudo apt install python3-tk")
        sys.exit(1)

    settings = load_settings()

    # Build env from settings
    env = {
        "LCO_HOST":               settings["host"],
        "LCO_PORT":               str(settings["port"]),
        "LCO_LOG_LEVEL":          "WARNING",
        "LCO_OPENAI_BASE_URL":    settings["openai_url"],
        "LCO_ANTHROPIC_BASE_URL": settings.get("anthropic_url", "https://api.anthropic.com"),
        "LCO_COMPRESSION_MODE":   settings["mode"],
        "LCO_OUTPUT_OPT":         "true" if settings["output_on"] else "false",
        "LCO_MEMORY_COMPRESSION": "true" if settings["memory_on"] else "false",
        "LCO_MEMORY_WINDOW":      str(settings.get("memory_window", 8)),
        "LCO_QUALITY_GATE":       "true",
        "LCO_QUALITY_THRESHOLD":  str(settings.get("threshold", 0.40)),
        "LCO_EMBEDDER":           settings.get("embedder", "tfidf"),
        "LCO_DB_PATH":            str(DB_PATH),
        # Inject the API key as the appropriate environment variable
        "OPENAI_API_KEY":         settings.get("api_key", ""),
    }

    threading.Thread(target=_server_thread, args=(env,),
                     daemon=True, name="lco-server").start()

    client = ProxyClient(settings["host"], settings["port"])
    for _ in range(40):
        if client.is_alive(): break
        time.sleep(0.25)

    root = tk.Tk()
    root.withdraw()
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    state    = AppState(settings)
    stop     = threading.Event()
    icon_ref: list = []

    icon = pystray.Icon("LCO",
                        _make_icon(client.is_alive(), settings["mode"]),
                        "LCO — LLM Context Optimizer",
                        _build_menu(state, client, icon_ref, root, settings, stop))
    icon_ref.append(icon)

    threading.Thread(target=_poll_loop,
                     args=(client, state, icon_ref, stop),
                     daemon=True, name="lco-poll").start()

    time.sleep(0.5)
    icon.run_detached()

    # Show first-run settings wizard automatically
    if settings.get("first_run", True):
        root.after(1500, lambda: _show_settings(root, settings, client,
                                                icon_ref, state, stop))

    print(f"[LCO] Running — data dir: {APP_DIR}")
    print(f"[LCO] Proxy: http://{settings['host']}:{settings['port']}")

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try: icon.stop()
        except Exception: pass
        client.close()
        print("[LCO] Stopped.")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    import sys, os
    _exe_dir = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))
    # Log goes to APP_DIR, not beside the exe (resolved after _app_data_dir runs)
    try:
        _app_data_dir().mkdir(parents=True, exist_ok=True)
        _log = open(_app_data_dir() / "lco.log", "w", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = _log
    except Exception:
        pass

    main()