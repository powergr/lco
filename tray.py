#!/usr/bin/env python3
"""
LCO — System Tray / Menu Bar App  v0.2.0
==========================================
Autonomous — no command-line arguments. All settings persisted to:
  Windows : %APPDATA%\\LCO\\settings.json
  macOS   : ~/Library/Application Support/LCO/settings.json
  Linux   : ~/.local/share/LCO/settings.json

Dependencies:
  pip install pystray Pillow
  Linux only: sudo apt install python3-tk libappindicator3-1
"""

from __future__ import annotations

import json
import os
import platform
import socket
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
# sys._MEIPASS is set by PyInstaller at runtime — unknown to type checkers.
_meipass: str | None = getattr(sys, "_MEIPASS", None)  # type: ignore[attr-defined]
if getattr(sys, "frozen", False) and _meipass:
    _bundle = Path(_meipass)
    if str(_bundle) not in sys.path:
        sys.path.insert(0, str(_bundle))
else:
    _HERE   = Path(__file__).resolve().parent
    _PARENT = _HERE.parent
    for _p in (_PARENT, _HERE):
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))

from lco.version import __version__ as VERSION

# ── Platform-aware app data directory ────────────────────────────────────────

def _app_data_dir() -> Path:
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

APP_DIR       = _app_data_dir()
SETTINGS_FILE = APP_DIR / "settings.json"
DB_PATH       = APP_DIR / "lco_metrics.db"
LOG_PATH      = APP_DIR / "lco.log"
LOCK_FILE     = APP_DIR / "lco.lock"      # single-instance guard

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict[str, Any] = {
    "provider":          "openai",
    "openai_url":        "https://api.openai.com",
    "anthropic_url":     "https://api.anthropic.com",
    "openai_key":        "",
    "anthropic_key":     "",
    "openrouter_key":    "",
    "groq_key":          "",
    "mistral_key":       "",
    "model":             "gpt-4o-mini",
    "mode":              "light",
    "output_on":         False,
    "memory_on":         False,
    "memory_window":     8,
    "threshold":         0.40,
    "embedder":          "tfidf",
    "port":              8000,
    "host":              "127.0.0.1",
    "start_with_os":     True,
    "first_run":         True,
}

PROVIDER_URLS: dict[str, str] = {
    "openai":     "https://api.openai.com",
    "anthropic":  "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai/api",
    "groq":       "https://api.groq.com/openai",
    "mistral":    "https://api.mistral.ai",
    "together":   "https://api.together.xyz",
    "deepseek":   "https://api.deepseek.com",
    "ollama":     "http://localhost:11434",
}

# Suggested models per provider — shown as hints, never enforced.
# Users type any model name they want; history is saved per-provider in settings.
PROVIDER_MODEL_HINTS: dict[str, list[str]] = {
    "openai":     ["gpt-4o-mini", "gpt-4o", "o1-mini", "gpt-3.5-turbo"],
    "anthropic":  ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-5"],
    # openrouter/free is a valid shorthand for a random free model.
    # Format for specific models: provider/model-name:free
    "openrouter": ["openrouter/free",
                   "openai/gpt-4o-mini",
                   "meta-llama/llama-3.1-8b-instruct:free",
                   "stepfun/step-3.5-flash:free",
                   "nvidia/nemotron-3-super-120b-a12b:free",
                   "google/gemma-2-9b-it:free",
                   "z-ai/glm-4.5-air:free"],
    "groq":       ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                   "mixtral-8x7b-32768", "gemma2-9b-it"],
    "mistral":    ["mistral-large-latest", "mistral-small-latest",
                   "codestral-latest", "mistral-nemo"],
    "together":   ["meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
                   "mistralai/Mixtral-8x7B-Instruct-v0.1"],
    "deepseek":   ["deepseek-chat", "deepseek-coder"],
    "ollama":     ["llama3.2", "qwen2.5:7b", "mistral", "phi3",
                   "deepseek-r1:7b", "gemma3:4b"],
}
# Keep backward compat alias
PROVIDER_MODELS = PROVIDER_MODEL_HINTS

# Key field name per provider (maps to settings dict key)
PROVIDER_KEY_FIELD: dict[str, str] = {
    "openai":     "openai_key",
    "anthropic":  "anthropic_key",
    "openrouter": "openrouter_key",
    "groq":       "groq_key",
    "mistral":    "mistral_key",
    "together":   "openrouter_key",  # uses the same key format
    "deepseek":   "openai_key",
    "ollama":     "",                # no key needed
}

MODES = ["passthrough", "light", "medium", "aggressive"]
POLL_INTERVAL = 3
ICON_SIZE     = 64
INPUT_PRICE_PER_M  = 2.50
OUTPUT_PRICE_PER_M = 10.00


# ── Settings ──────────────────────────────────────────────────────────────────

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
        print(f"[LCO] Cannot save settings: {e}")

def _active_key(settings: dict[str, Any]) -> str:
    """Return the API key for the currently selected provider."""
    field = PROVIDER_KEY_FIELD.get(settings.get("provider", "openai"), "openai_key")
    return settings.get(field, "") if field else ""


# ── Single-instance guard ─────────────────────────────────────────────────────

class _InstanceLock:
    """
    Prevents two copies of LCO from running simultaneously.
    Uses a TCP socket bound to localhost:port+1 as a lock.
    Falls back to a lock file if the socket approach fails.
    """
    def __init__(self, port: int) -> None:
        self._port = port + 10000   # well away from the proxy port
        self._sock: socket.socket | None = None

    def acquire(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind(("127.0.0.1", self._port))
            s.listen(1)
            self._sock = s
            return True
        except OSError:
            return False  # already running

    def release(self) -> None:
        if self._sock:
            try: self._sock.close()
            except Exception: pass


# ── Port utilities ────────────────────────────────────────────────────────────

def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0

def _find_free_port(start: int = 8000) -> int:
    for p in range(start, start + 20):
        if _port_free(p):
            return p
    return start


# ── OS startup registration ───────────────────────────────────────────────────

def _set_startup(enabled: bool) -> None:
    if platform.system() != "Windows":
        return
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        exe = sys.executable if getattr(sys, "frozen", False) else ""
        if enabled and exe:
            winreg.SetValueEx(key, "LCO", 0, winreg.REG_SZ, exe)
        else:
            try: winreg.DeleteValue(key, "LCO")
            except FileNotFoundError: pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[LCO] Startup registration failed: {e}")


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
    dot = {"passthrough":(150,150,150,255),"light":(255,200,50,255),
           "medium":(100,140,255,255),"aggressive":(255,80,80,255)
           }.get(mode,(150,150,150,255))
    if running:
        r = 9
        draw.ellipse([ICON_SIZE-r*2-1,ICON_SIZE-r*2-1,
                      ICON_SIZE-1, ICON_SIZE-1], fill=dot)
    bg = Image.new("RGB", (ICON_SIZE, ICON_SIZE), (30, 30, 30))
    bg.paste(canvas, mask=canvas.split()[3])
    return bg


# ── Cost helpers ──────────────────────────────────────────────────────────────

def _to_dollars(in_saved: int, out_saved: int) -> float:
    return (in_saved/1_000_000*INPUT_PRICE_PER_M +
            out_saved/1_000_000*OUTPUT_PRICE_PER_M)

def _fmt_dollars(v: float) -> str:
    return f"${v:.4f}" if v >= 0.01 else f"${v*100:.3f}¢"


# ── Proxy client ──────────────────────────────────────────────────────────────

class ProxyClient:
    def __init__(self, host: str, port: int):
        self.base = f"http://{host}:{port}"
        self._c   = httpx.Client(timeout=5)

    def is_alive(self) -> bool:
        try: return self._c.get(f"{self.base}/health").status_code == 200
        except Exception: return False

    def status(self) -> dict[str, Any]:
        try: return self._c.get(f"{self.base}/lco/status").json()
        except Exception: return {}

    def set(self, **kw: Any) -> None:
        try: self._c.post(f"{self.base}/lco/control", json=kw)
        except Exception: pass

    def test_connection(self, model: str, api_key: str,
                        openai_url: str) -> tuple[bool, str, float]:
        """Send a minimal test request. Returns (ok, message, latency_ms)."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with: ok"}],
            "max_tokens": 5, "stream": False,
        }
        t0 = time.perf_counter()
        try:
            r = self._c.post(f"{self.base}/v1/chat/completions",
                             json=body, headers=headers, timeout=30)
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                return True, f"Connected  {ms:.0f} ms", ms
            try:
                err = r.json().get("error", {}).get("message", r.text[:80])
            except Exception:
                err = r.text[:80]
            return False, f"HTTP {r.status_code}: {err}", ms
        except httpx.TimeoutException:
            ms = (time.perf_counter() - t0) * 1000
            return False, "Timeout — check model name and API key", ms
        except Exception as exc:
            return False, str(exc)[:80], 0.0

    def dashboard_url(self) -> str: return f"{self.base}/lco/dashboard"
    def close(self) -> None:
        try: self._c.close()
        except Exception: pass


# ── Shared state ──────────────────────────────────────────────────────────────

class AppState:
    def __init__(self, settings: dict[str, Any]):
        self.lock        = threading.Lock()
        self.running     = False
        self.mode        = settings["mode"]
        self.output_on   = settings["output_on"]
        self.total_in_saved  = 0
        self.total_out_saved = 0
        self.total_requests  = 0
        self.session_dollars = 0.0
        self.total_dollars   = 0.0
        self._base_in  = -1
        self._base_out = -1

    def update(self, data: dict[str, Any]) -> None:
        m    = data.get("metrics") or {}
        in_s  = int(m.get("total_input_saved")  or 0)
        out_s = int(m.get("total_output_saved") or 0)
        with self.lock:
            self.running     = True
            self.mode        = data.get("compression_mode", self.mode)
            self.output_on   = bool(data.get("output_optimization"))
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
        if client.is_alive():
            data = client.status()
            if data: state.update(data)
        else:
            state.mark_stopped()
        if icon_ref:
            try: icon_ref[0].icon = _make_icon(state.running, state.mode)
            except Exception: pass
        stop.wait(POLL_INTERVAL)


# ── Theme helpers ─────────────────────────────────────────────────────────────

BG      = "#1a1d27"
SURFACE = "#21253a"
TEXT    = "#e2e8f0"
MUTED   = "#8892a4"
GREEN   = "#4ade80"
ACCENT  = "#6c8aff"
AMBER   = "#fbbf24"
RED_C   = "#f87171"
MODE_COL = {"passthrough":MUTED,"light":AMBER,"medium":ACCENT,"aggressive":RED_C}


def _fonts() -> dict[str, Any]:
    from tkinter import font as tkfont
    return {
        "bold":  tkfont.Font(family="Segoe UI", size=11, weight="bold"),
        "head":  tkfont.Font(family="Segoe UI", size=12, weight="bold"),
        "label": tkfont.Font(family="Segoe UI", size=9),
        "big":   tkfont.Font(family="Segoe UI", size=18, weight="bold"),
        "mid":   tkfont.Font(family="Segoe UI", size=13, weight="bold"),
        "mono":  tkfont.Font(family="Consolas",  size=9),
    }

def _centre(win: Any) -> None:
    win.update_idletasks()
    sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
    w  = win.winfo_reqwidth();   h  = win.winfo_reqheight()
    win.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

def _lbl(parent: Any, text: str, font: Any, fg: str = MUTED) -> Any:
    import tkinter as tk
    return tk.Label(parent, text=text, font=font, fg=fg,
                    bg=parent.cget("bg"))

def _section_header(win: Any, text: str, font: Any) -> None:
    import tkinter as tk
    f = tk.Frame(win, bg=BG)
    f.pack(fill="x", padx=16, pady=(10, 2))
    tk.Label(f, text=text, font=font, fg=ACCENT, bg=BG).pack(anchor="w")
    tk.Frame(win, bg=SURFACE, height=1).pack(fill="x", padx=16, pady=(0, 4))


# ── Settings window ───────────────────────────────────────────────────────────

def _show_settings(root: Any, settings: dict[str, Any],
                   client: ProxyClient, icon_ref: list,
                   state: AppState, stop: threading.Event,
                   on_save: Any | None = None) -> None:
    import tkinter as tk
    from tkinter import messagebox

    win = tk.Toplevel(root)
    win.title("LCO — Settings")
    win.resizable(False, False)
    win.configure(bg=BG)
    F = _fonts()

    def _row(parent: Any, label: str, widget_fn: Any) -> Any:
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=16, pady=3)
        tk.Label(f, text=label, font=F["label"], fg=MUTED, bg=BG,
                 width=18, anchor="w").grid(row=0, column=0, sticky="w")
        w = widget_fn(f)
        w.grid(row=0, column=1, sticky="w", padx=(0, 4))
        return w

    def _entry(parent: Any, var: Any, width: int = 36,
               show: str = "") -> tk.Entry:
        return tk.Entry(parent, textvariable=var, font=F["mono"],
                        bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                        relief="flat", width=width, show=show)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr = tk.Frame(win, bg=SURFACE, pady=12)
    hdr.pack(fill="x")
    tk.Label(hdr, text="⚡ LCO Settings", font=F["head"],
             fg=ACCENT, bg=SURFACE).pack(side="left", padx=16)
    tk.Label(hdr, text=f"v{VERSION}", font=F["label"],
             fg=MUTED, bg=SURFACE).pack(side="right", padx=16)

    # ── Provider ──────────────────────────────────────────────────────────────
    _section_header(win, "Provider", F["bold"])
    pf = tk.Frame(win, bg=BG); pf.pack(fill="x", padx=16, pady=2)

    provider_var = tk.StringVar(value=settings.get("provider","openai"))
    url_var      = tk.StringVar(value=settings.get("openai_url","https://api.openai.com"))

    # Per-provider model history stored in settings["provider_models"]
    # Falls back to hints if no history exists
    provider_models_hist: dict[str, list[str]] = settings.get("provider_models", {})

    def _current_model_for(p: str) -> str:
        """Return the last-used model for provider p."""
        hist = provider_models_hist.get(p, [])
        if hist:
            return hist[0]
        hints = PROVIDER_MODEL_HINTS.get(p, [])
        return hints[0] if hints else ""

    model_var = tk.StringVar(value=_current_model_for(settings.get("provider","openai")))

    # ── Provider row ──────────────────────────────────────────────────────────
    tk.Label(pf, text="Provider", font=F["label"], fg=MUTED, bg=BG,
             width=18, anchor="w").grid(row=0, column=0, sticky="w")
    prov_menu = tk.OptionMenu(pf, provider_var, *PROVIDER_URLS.keys())
    prov_menu.config(font=F["label"], bg=SURFACE, fg=TEXT,
                     activebackground=ACCENT, relief="flat",
                     highlightthickness=0, width=14)
    prov_menu.grid(row=0, column=1, sticky="w")

    # ── URL row ───────────────────────────────────────────────────────────────
    tk.Label(pf, text="URL", font=F["label"], fg=MUTED, bg=BG,
             width=18, anchor="w").grid(row=1, column=0, sticky="w", pady=3)
    url_entry = _entry(pf, url_var)
    url_entry.grid(row=1, column=1, sticky="w")

    # ── Model row — single editable field with dropdown history ───────────────
    tk.Label(pf, text="Model", font=F["label"], fg=MUTED, bg=BG,
             width=18, anchor="w").grid(row=2, column=0, sticky="w", pady=3)

    model_frame = tk.Frame(pf, bg=BG)
    model_frame.grid(row=2, column=1, sticky="w", columnspan=2)

    model_entry = tk.Entry(model_frame, textvariable=model_var, font=F["mono"],
                           bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                           relief="flat", width=30)
    model_entry.pack(side="left")

    # Dropdown arrow button — shows history + hints for this provider
    def _show_model_picker() -> None:
        p     = provider_var.get()
        hist  = provider_models_hist.get(p, [])
        hints = PROVIDER_MODEL_HINTS.get(p, [])
        # Combined list: history first (de-duped), then hints not already shown
        combined: list[str] = list(dict.fromkeys(hist + hints))

        picker = tk.Toplevel(win)
        picker.title("")
        picker.overrideredirect(True)        # borderless
        picker.configure(bg=SURFACE)

        lb = tk.Listbox(picker, font=F["mono"], bg=SURFACE, fg=TEXT,
                        selectbackground=ACCENT, selectforeground="#000",
                        relief="flat", bd=0, width=32,
                        height=min(len(combined), 10))
        lb.pack(padx=1, pady=1)
        for item in combined:
            lb.insert("end", item)

        # Mark history items differently
        for i, item in enumerate(combined):
            if item in hist:
                lb.itemconfig(i, fg=TEXT)
            else:
                lb.itemconfig(i, fg=MUTED)

        def _pick(evt: Any = None) -> None:
            sel = lb.curselection()
            if sel:
                model_var.set(lb.get(sel[0]))
            picker.destroy()

        lb.bind("<Return>",      _pick)
        lb.bind("<Double-1>",    _pick)
        lb.bind("<FocusOut>",    lambda e: picker.destroy())
        lb.bind("<Escape>",      lambda e: picker.destroy())

        # Position below the model entry
        model_entry.update_idletasks()
        x = model_entry.winfo_rootx()
        y = model_entry.winfo_rooty() + model_entry.winfo_height()
        picker.geometry(f"+{x}+{y}")
        lb.focus_set()

    tk.Button(model_frame, text="▾", font=F["label"],
              bg=SURFACE, fg=TEXT, relief="flat", bd=0,
              padx=6, cursor="hand2",
              command=_show_model_picker).pack(side="left", padx=(2,0))

    # Hint label that updates with provider
    hint_var = tk.StringVar()
    hint_lbl = tk.Label(pf, textvariable=hint_var, font=F["label"],
                        fg=MUTED, bg=BG)
    hint_lbl.grid(row=3, column=1, sticky="w", pady=(0,2))

    def _refresh_models() -> None:
        p = provider_var.get()
        # Set model to last used for this provider
        model_var.set(_current_model_for(p))
        # Update hint
        hints = PROVIDER_MODEL_HINTS.get(p, [])
        if hints:
            hint_var.set(f"e.g. {hints[0]}")
        else:
            hint_var.set("")

    def _on_provider(*_: Any) -> None:
        p = provider_var.get()
        url_var.set(PROVIDER_URLS.get(p, ""))
        _refresh_models()
        _refresh_key_section()

    provider_var.trace_add("write", _on_provider)
    _refresh_models()

    # ── API Keys (one field per provider that needs a key) ────────────────────
    _section_header(win, "API Keys", F["bold"])
    kf = tk.Frame(win, bg=BG); kf.pack(fill="x", padx=16, pady=2)

    key_labels = {
        "openai_key":     "OpenAI",
        "anthropic_key":  "Anthropic",
        "openrouter_key": "OpenRouter / Together",
        "groq_key":       "Groq",
        "mistral_key":    "Mistral",
    }
    key_vars: dict[str, tk.StringVar] = {}
    key_entries: dict[str, Any] = {}

    for row_idx, (field, label) in enumerate(key_labels.items()):
        kv = tk.StringVar(value=settings.get(field, ""))
        key_vars[field] = kv
        tk.Label(kf, text=label, font=F["label"], fg=MUTED, bg=BG,
                 width=22, anchor="w").grid(row=row_idx, column=0, sticky="w", pady=2)
        e = tk.Entry(kf, textvariable=kv, font=F["mono"], show="•",
                     bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                     relief="flat", width=34)
        e.grid(row=row_idx, column=1, sticky="w", pady=2)
        key_entries[field] = e

    # show/hide all keys toggle
    show_keys_var = tk.BooleanVar(value=False)
    def _toggle_show() -> None:
        ch = "" if show_keys_var.get() else "•"
        for e in key_entries.values():
            e.config(show=ch)
    tk.Checkbutton(kf, text="Show keys", variable=show_keys_var,
                   command=_toggle_show, font=F["label"], bg=BG, fg=MUTED,
                   activebackground=BG, selectcolor=SURFACE
                   ).grid(row=len(key_labels), column=1, sticky="w", pady=(4,0))

    # Highlight the key field for the active provider
    def _refresh_key_section() -> None:
        p     = provider_var.get()
        field = PROVIDER_KEY_FIELD.get(p, "")
        for f2, e in key_entries.items():
            e.config(bg=ACCENT if f2 == field else SURFACE,
                     fg="#000" if f2 == field else TEXT)

    _refresh_key_section()

    # ── Compression ───────────────────────────────────────────────────────────
    _section_header(win, "Compression", F["bold"])
    cf = tk.Frame(win, bg=BG); cf.pack(fill="x", padx=16, pady=4)

    mode_var = tk.StringVar(value=settings.get("mode","light"))
    tk.Label(cf, text="Mode", font=F["label"], fg=MUTED, bg=BG,
             width=18, anchor="w").grid(row=0, column=0, sticky="w", pady=2)
    btn_row = tk.Frame(cf, bg=BG); btn_row.grid(row=0, column=1, sticky="w")
    mode_btns: dict[str, Any] = {}
    for _m in MODES:
        b = tk.Button(btn_row, text=_m.capitalize(), font=F["label"],
                      relief="flat", bd=0, padx=8, pady=3, cursor="hand2",
                      command=lambda __m=_m: _pick_mode(__m))
        b.pack(side="left", padx=2)
        mode_btns[_m] = b

    def _pick_mode(m: str) -> None:
        mode_var.set(m)
        for _m2, btn in mode_btns.items():
            a = _m2 == m
            btn.configure(bg=MODE_COL.get(_m2,MUTED) if a else SURFACE,
                          fg="#000" if a else MUTED)
    _pick_mode(mode_var.get())

    out_var = tk.BooleanVar(value=settings.get("output_on", False))
    mem_var = tk.BooleanVar(value=settings.get("memory_on", False))
    for row_i, (lbl, var) in enumerate([("Output compression", out_var),
                                         ("Memory compression", mem_var)], 1):
        tk.Label(cf, text=lbl, font=F["label"], fg=MUTED, bg=BG,
                 width=18, anchor="w").grid(row=row_i, column=0, sticky="w", pady=2)
        tk.Checkbutton(cf, variable=var, bg=BG, fg=TEXT,
                       activebackground=BG, selectcolor=SURFACE,
                       font=F["label"]).grid(row=row_i, column=1, sticky="w")

    # ── Proxy / System ────────────────────────────────────────────────────────
    _section_header(win, "Proxy & System", F["bold"])
    sf = tk.Frame(win, bg=BG); sf.pack(fill="x", padx=16, pady=4)

    port_var    = tk.StringVar(value=str(settings.get("port", 8000)))
    startup_var = tk.BooleanVar(value=settings.get("start_with_os", True))

    tk.Label(sf, text="Listen port", font=F["label"], fg=MUTED, bg=BG,
             width=18, anchor="w").grid(row=0, column=0, sticky="w", pady=2)
    tk.Entry(sf, textvariable=port_var, font=F["mono"],
             bg=SURFACE, fg=TEXT, insertbackground=TEXT,
             relief="flat", width=8).grid(row=0, column=1, sticky="w")
    tk.Label(sf, text=f"  →  http://127.0.0.1:{port_var.get()}/v1",
             font=F["label"], fg=MUTED, bg=BG).grid(row=0, column=2, sticky="w")

    if platform.system() == "Windows":
        tk.Label(sf, text="Start with Windows", font=F["label"], fg=MUTED, bg=BG,
                 width=18, anchor="w").grid(row=1, column=0, sticky="w", pady=2)
        tk.Checkbutton(sf, variable=startup_var, bg=BG, fg=TEXT,
                       activebackground=BG, selectcolor=SURFACE,
                       font=F["label"]).grid(row=1, column=1, sticky="w")

    # ── Connection test ───────────────────────────────────────────────────────
    _section_header(win, "Test Connection", F["bold"])
    tf = tk.Frame(win, bg=BG); tf.pack(fill="x", padx=16, pady=4)

    test_result = tk.StringVar(value="Click Test to verify your API key and model.")
    test_lbl    = tk.Label(tf, textvariable=test_result, font=F["mono"],
                           fg=MUTED, bg=BG, wraplength=420, justify="left")
    test_lbl.pack(anchor="w", pady=(0, 6))

    def _run_test() -> None:
        test_result.set("Testing…")
        test_lbl.config(fg=AMBER)
        win.update()
        p     = provider_var.get()
        field = PROVIDER_KEY_FIELD.get(p, "openai_key")
        key   = key_vars.get(field, tk.StringVar()).get().strip() if field else "no-key"
        model = model_var.get().strip()  # single editable field

        def _do() -> None:
            ok, msg, _ = client.test_connection(model, key, url_var.get())
            def _apply() -> None:
                test_result.set(f"{'✓' if ok else '✗'} {msg}")
                test_lbl.config(fg=GREEN if ok else RED_C)
            win.after(0, _apply)

        threading.Thread(target=_do, daemon=True).start()

    tk.Button(tf, text="▶  Test connection", font=F["label"],
              bg=ACCENT, fg="#000", relief="flat", padx=14, pady=5,
              cursor="hand2", command=_run_test).pack(anchor="w")

    # ── Save / Cancel ─────────────────────────────────────────────────────────
    af = tk.Frame(win, bg=SURFACE, pady=10); af.pack(fill="x", pady=(10,0))
    status_lbl = tk.Label(af, text="", font=F["label"], fg=GREEN, bg=SURFACE)
    status_lbl.pack(side="left", padx=16)

    def _save() -> None:
        model_final = model_var.get().strip()
        p = provider_var.get()
        port_int = int(port_var.get() or 8000)

        # Save model into per-provider history (most-recent first, max 8)
        hist = dict(provider_models_hist)
        prov_hist = [m for m in hist.get(p, []) if m != model_final]
        hist[p] = ([model_final] + prov_hist)[:8]
        provider_models_hist.update(hist)

        new_s = dict(settings)
        new_s.update({
            "provider":        p,
            "openai_url":      url_var.get().strip(),
            "model":           model_final,
            "provider_models": hist,
            "mode":        mode_var.get(),
            "output_on":   out_var.get(),
            "memory_on":   mem_var.get(),
            "port":        port_int,
            "start_with_os": startup_var.get(),
            "first_run":   False,
        })
        # Save all key fields
        for field in key_vars:
            new_s[field] = key_vars[field].get().strip()

        save_settings(new_s)
        settings.update(new_s)

        if platform.system() == "Windows":
            _set_startup(startup_var.get())

        # Apply runtime changes immediately
        client.set(compression_mode=new_s["mode"],
                   output_optimization=new_s["output_on"],
                   memory_compression=new_s["memory_on"])
        state.mode      = new_s["mode"]
        state.output_on = new_s["output_on"]
        if icon_ref:
            # Use the original hidden root, NOT win.winfo_toplevel().
            # win is destroyed 1.2s later — any menu holding a reference
            # to it will fail silently on all subsequent .after() calls.
            icon_ref[0].menu = _build_menu(state,client,icon_ref,
                                           root,settings,stop)
        status_lbl.config(text="✓ Saved")
        win.after(1200, win.destroy)
        if on_save:
            on_save(new_s)

    tk.Button(af, text="Save & Apply", font=F["label"],
              bg=GREEN, fg="#000", relief="flat", padx=16, pady=6,
              cursor="hand2", command=_save).pack(side="right", padx=8)
    tk.Button(af, text="Cancel", font=F["label"],
              bg=SURFACE, fg=MUTED, relief="flat", padx=12, pady=6,
              cursor="hand2", command=win.destroy).pack(side="right")

    _centre(win)


# ── Status popup ──────────────────────────────────────────────────────────────

def _show_status(root: Any, state: AppState, client: ProxyClient,
                 icon_ref: list, settings: dict[str, Any],
                 stop: threading.Event) -> None:
    import tkinter as tk

    popup = tk.Toplevel(root)
    popup.title("LCO — Status")
    popup.resizable(False, False)
    popup.configure(bg=BG)
    F = _fonts()

    hdr = tk.Frame(popup, bg=SURFACE, pady=10); hdr.pack(fill="x")
    tk.Label(hdr, text="⚡ LCO", font=F["bold"], fg=ACCENT,
             bg=SURFACE).pack(side="left",  padx=16)
    dot = tk.Label(hdr, text="●", font=F["label"], bg=SURFACE)
    dot.pack(side="left")
    tk.Label(hdr, text=f"v{VERSION}", font=F["label"],
             fg=MUTED, bg=SURFACE).pack(side="right", padx=16)

    # Savings
    sf = tk.Frame(popup, bg=BG, pady=8); sf.pack(fill="x", padx=16, pady=(12,4))
    tk.Label(sf, text="💰 Saved this session", font=F["label"],
             fg=MUTED, bg=BG).pack(anchor="w")
    sess_lbl = tk.Label(sf, text="$0.0000", font=F["big"], fg=GREEN, bg=BG)
    sess_lbl.pack(anchor="w")
    tk.Label(sf, text="All-time", font=F["label"],
             fg=MUTED, bg=BG).pack(anchor="w", pady=(8,0))
    all_lbl  = tk.Label(sf, text="$0.0000", font=F["mid"], fg=GREEN, bg=BG)
    all_lbl.pack(anchor="w")

    # Token breakdown
    tf2 = tk.Frame(popup, bg=SURFACE, padx=16, pady=8); tf2.pack(fill="x", pady=4)
    in_lbl  = tk.Label(tf2, text="Input saved:  —", font=F["mono"],
                        fg=TEXT, bg=SURFACE)
    out_lbl = tk.Label(tf2, text="Output saved: —", font=F["mono"],
                        fg=TEXT, bg=SURFACE)
    req_lbl = tk.Label(tf2, text="Requests:     —", font=F["mono"],
                        fg=MUTED, bg=SURFACE)
    in_lbl.pack(anchor="w"); out_lbl.pack(anchor="w"); req_lbl.pack(anchor="w")

    # Proxy URL (copy to clipboard)
    cf2 = tk.Frame(popup, bg=BG); cf2.pack(fill="x", padx=16, pady=4)
    proxy_url = f"http://{settings['host']}:{settings['port']}/v1"
    tk.Label(cf2, text="Proxy URL", font=F["label"], fg=MUTED, bg=BG
             ).pack(side="left")
    url_lbl = tk.Label(cf2, text=proxy_url, font=F["mono"], fg=ACCENT, bg=BG)
    url_lbl.pack(side="left", padx=8)

    def _copy_url() -> None:
        popup.clipboard_clear()
        popup.clipboard_append(proxy_url)
        copy_btn.config(text="✓ Copied")
        popup.after(1500, lambda: copy_btn.config(text="📋 Copy"))

    copy_btn = tk.Button(cf2, text="📋 Copy", font=F["label"],
                         bg=SURFACE, fg=ACCENT, relief="flat",
                         padx=8, pady=2, cursor="hand2", command=_copy_url)
    copy_btn.pack(side="right")

    # Mode buttons
    mf2 = tk.Frame(popup, bg=BG, pady=6); mf2.pack(fill="x", padx=16, pady=4)
    tk.Label(mf2, text="Compression Mode  (click to change)",
             font=F["label"], fg=MUTED, bg=BG).pack(anchor="w", pady=(0,4))
    br = tk.Frame(mf2, bg=BG); br.pack(fill="x")
    mode_btns: dict[str, Any] = {}
    for m in MODES:
        b = tk.Button(br, text=m.capitalize(), font=F["label"], relief="flat",
                      bd=0, padx=8, pady=4, cursor="hand2",
                      command=lambda _m=m: _set_mode(_m))
        b.pack(side="left", padx=2); mode_btns[m] = b

    of = tk.Frame(popup, bg=BG); of.pack(fill="x", padx=16, pady=2)
    tk.Label(of, text="Output compression  (click to toggle)",
             font=F["label"], fg=MUTED, bg=BG).pack(side="left")
    out_btn = tk.Button(of, text="OFF", font=F["label"], relief="flat",
                        bd=0, padx=8, pady=3, cursor="hand2",
                        command=lambda: _toggle_out())
    out_btn.pack(side="right")

    # Action bar
    af2 = tk.Frame(popup, bg=SURFACE, pady=8); af2.pack(fill="x", pady=(8,0))
    tk.Button(af2, text="📊 Dashboard", font=F["label"], relief="flat", bd=0,
              fg=ACCENT, bg=SURFACE, padx=12, pady=6, cursor="hand2",
              command=lambda: webbrowser.open(client.dashboard_url())
              ).pack(side="left", padx=8)
    tk.Button(af2, text="⚙ Settings", font=F["label"], relief="flat", bd=0,
              fg=MUTED, bg=SURFACE, padx=12, pady=6, cursor="hand2",
              command=lambda: (popup.destroy(),
                               root.after(0, lambda: _show_settings(
                                   root,settings,client,icon_ref,state,stop)))
              ).pack(side="left", padx=4)
    tk.Button(af2, text="✕", font=F["label"], relief="flat", bd=0,
              fg=MUTED, bg=SURFACE, padx=12, pady=6, cursor="hand2",
              command=popup.destroy).pack(side="right", padx=8)

    # pending_* tracks what the user just clicked so _tick doesn't fight it
    pending_mode: list[str] = [state.mode]
    pending_out:  list[bool] = [state.output_on]

    def _upd_btns(cur: str) -> None:
        for _m, btn in mode_btns.items():
            a = _m == cur
            col = MODE_COL.get(_m, MUTED)
            btn.configure(
                bg=col          if a else SURFACE,
                fg="#000"       if a else MUTED,
                relief="solid"  if a else "flat",
                bd=1            if a else 0,
            )

    def _set_mode(m: str) -> None:
        pending_mode[0] = m
        client.set(compression_mode=m)
        state.mode = m
        _upd_btns(m)
        settings["mode"] = m
        save_settings(settings)
        if icon_ref:
            icon_ref[0].menu = _build_menu(state,client,icon_ref,
                                           root,settings,stop)

    def _toggle_out() -> None:
        new = not pending_out[0]
        pending_out[0] = new
        client.set(output_optimization=new)
        state.output_on = new
        settings["output_on"] = new
        save_settings(settings)
        out_btn.configure(text="ON ✓" if new else "OFF",
                          bg=GREEN if new else SURFACE,
                          fg="#000" if new else MUTED)

    def _tick() -> None:
        with state.lock:
            running=state.running; sess=state.session_dollars
            total=state.total_dollars; in_s=state.total_in_saved
            out_s=state.total_out_saved; reqs=state.total_requests
            server_mode=state.mode
        # Sync pending state with server once the poll confirms the change
        if server_mode == pending_mode[0]:
            _upd_btns(pending_mode[0])
        dot.configure(fg=GREEN if running else RED_C)
        sess_lbl.configure(text=_fmt_dollars(sess))
        all_lbl.configure(text=_fmt_dollars(total))
        in_lbl.configure(text=f"Input saved:  {in_s:,} tokens")
        out_lbl.configure(text=f"Output saved: {out_s:,} tokens")
        req_lbl.configure(text=f"Requests:     {reqs:,}")
        # Only refresh out_btn from server if it matches pending (avoids flicker)
        if state.output_on == pending_out[0]:
            out_btn.configure(text="ON ✓" if state.output_on else "OFF",
                              bg=GREEN if state.output_on else SURFACE,
                              fg="#000" if state.output_on else MUTED)
        if popup.winfo_exists(): popup.after(2000, _tick)

    _upd_btns(state.mode)
    out_btn.configure(text="ON ✓" if state.output_on else "OFF",
                      bg=GREEN if state.output_on else SURFACE,
                      fg="#000" if state.output_on else MUTED)
    _tick()
    _centre(popup)


# ── Startup failure dialog ────────────────────────────────────────────────────

def _show_startup_error(root: Any, port: int) -> None:
    import tkinter as tk
    from tkinter import messagebox
    messagebox.showerror(
        "LCO — Startup Failed",
        f"The LCO proxy could not start on port {port}.\n\n"
        "Possible causes:\n"
        "  • Another application is using that port\n"
        "  • A previous LCO instance is still running\n\n"
        "Go to Settings and choose a different port, then restart LCO.",
        parent=root,
    )


# ── Tray menu ─────────────────────────────────────────────────────────────────

def _build_menu(state: AppState, client: ProxyClient, icon_ref: list,
                root: Any, settings: dict[str, Any],
                stop: threading.Event) -> Any:
    import pystray

    def _mode_item(m: str) -> Any:
        chk  = "✓ " if state.mode == m else "  "
        desc = {"passthrough":"Off","light":"Light",
                "medium":"Medium","aggressive":"Max"}[m]
        return pystray.MenuItem(f"{chk}{desc}", lambda: _switch(m))

    def _switch(m: str) -> None:
        client.set(compression_mode=m); state.mode = m
        settings["mode"] = m; save_settings(settings)
        if icon_ref:
            icon_ref[0].menu = _build_menu(state,client,icon_ref,
                                           root,settings,stop)

    def _toggle_out() -> None:
        new = not state.output_on; client.set(output_optimization=new)
        state.output_on = new; settings["output_on"] = new; save_settings(settings)
        if icon_ref:
            icon_ref[0].menu = _build_menu(state,client,icon_ref,
                                           root,settings,stop)

    with state.lock: sess = state.session_dollars
    saved_str = f"💰 {_fmt_dollars(sess)} saved"
    out_lbl   = f"{'✓' if state.output_on else '  '} Output compression"

    return pystray.Menu(
        pystray.MenuItem("⚡ LCO", None, enabled=False),
        pystray.MenuItem(saved_str, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Mode", pystray.Menu(*[_mode_item(m) for m in MODES])),
        pystray.MenuItem(out_lbl, _toggle_out),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("📊 Status & Savings...",
                         lambda: root.after(0, lambda: _show_status(
                             root,state,client,icon_ref,settings,stop))),
        pystray.MenuItem("⚙ Settings...",
                         lambda: root.after(0, lambda: _show_settings(
                             root,settings,client,icon_ref,state,stop))),
        pystray.MenuItem("🌐 Open Dashboard",
                         lambda: webbrowser.open(client.dashboard_url())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit LCO",
                         lambda: (icon_ref[0].stop() if icon_ref else None,
                                  root.after(0, root.quit))),
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        import pystray
    except ImportError:
        print("pystray not installed:  pip install pystray Pillow"); sys.exit(1)
    try:
        import tkinter as tk
    except ImportError:
        print("tkinter missing. Linux: sudo apt install python3-tk"); sys.exit(1)

    settings = load_settings()

    # ── Single instance guard ─────────────────────────────────────────────────
    lock = _InstanceLock(settings["port"])
    if not lock.acquire():
        # Already running — just show a notification and exit
        root = tk.Tk(); root.withdraw()
        from tkinter import messagebox
        messagebox.showinfo("LCO", "LCO is already running.\n"
                            "Check your system tray.")
        root.destroy()
        return

    # ── Port conflict detection ───────────────────────────────────────────────
    port = settings["port"]
    if not _port_free(port):
        new_port = _find_free_port(port + 1)
        root = tk.Tk(); root.withdraw()
        from tkinter import messagebox
        answer = messagebox.askyesno(
            "LCO — Port Conflict",
            f"Port {port} is already in use.\n\n"
            f"Use port {new_port} instead?",
        )
        if answer:
            settings["port"] = new_port
            save_settings(settings)
            port = new_port
        else:
            root.destroy(); lock.release(); return

    # ── Start proxy server ────────────────────────────────────────────────────
    env = {
        "LCO_HOST":               settings["host"],
        "LCO_PORT":               str(port),
        "LCO_LOG_LEVEL":          "WARNING",
        "LCO_OPENAI_BASE_URL":    settings.get("openai_url","https://api.openai.com"),
        "LCO_ANTHROPIC_BASE_URL": settings.get("anthropic_url","https://api.anthropic.com"),
        "LCO_COMPRESSION_MODE":   settings["mode"],
        "LCO_OUTPUT_OPT":         "true" if settings["output_on"] else "false",
        "LCO_MEMORY_COMPRESSION": "true" if settings["memory_on"] else "false",
        "LCO_MEMORY_WINDOW":      str(settings.get("memory_window",8)),
        "LCO_QUALITY_GATE":       "true",
        "LCO_QUALITY_THRESHOLD":  str(settings.get("threshold",0.40)),
        "LCO_EMBEDDER":           settings.get("embedder","tfidf"),
        "LCO_DB_PATH":            str(DB_PATH),
        "OPENAI_API_KEY":         settings.get("openai_key",""),
        "ANTHROPIC_API_KEY":      settings.get("anthropic_key",""),
        # Active provider key — injected as fallback when client sends no auth
        "LCO_API_KEY":            _active_key(settings),
    }
    threading.Thread(target=_server_thread, args=(env,),
                     daemon=True, name="lco-server").start()

    # ── Wait for proxy (max 10s) with startup failure detection ───────────────
    client = ProxyClient(settings["host"], port)
    alive  = False
    for _ in range(40):
        if client.is_alive(): alive = True; break
        time.sleep(0.25)

    root = tk.Tk()
    root.withdraw()
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    if not alive:
        _show_startup_error(root, port)
        lock.release(); return

    # ── Tray icon ─────────────────────────────────────────────────────────────
    state    = AppState(settings)
    stop     = threading.Event()
    icon_ref: list = []

    icon = pystray.Icon("LCO",
                        _make_icon(True, settings["mode"]),
                        "LCO — LLM Context Optimizer",
                        _build_menu(state,client,icon_ref,root,settings,stop))
    icon_ref.append(icon)

    threading.Thread(target=_poll_loop,
                     args=(client,state,icon_ref,stop),
                     daemon=True, name="lco-poll").start()

    time.sleep(0.5)
    icon.run_detached()

    # First-run wizard
    if settings.get("first_run", True):
        root.after(1500, lambda: _show_settings(root,settings,client,
                                                icon_ref,state,stop))

    print(f"[LCO] Running  data={APP_DIR}  port={port}")

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try: icon.stop()
        except Exception: pass
        client.close()
        lock.release()
        print("[LCO] Stopped.")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    try:
        _app_data_dir().mkdir(parents=True, exist_ok=True)
        _log = open(_app_data_dir() / "lco.log", "w", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = _log
    except Exception:
        pass

    main()