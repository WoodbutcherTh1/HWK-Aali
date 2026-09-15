"""Aali Node shell — the GUI half of STEP 5: pywebview window + system tray.

The shell is UI ONLY on top of exactly the headless daemon's calls: it starts
``daemon.run_node`` in a thread (``native_confirm=True`` — the shell is the
GUI mode) and polls the content-free ``status`` dict the daemon publishes.
No state lives here that the daemon did not report.

Launch (the daemon CLI routes ``--shell`` here)::

    python -m aali_node --shell --hub ws://127.0.0.1:8080 --token <JWT>

Design notes
------------
- Arabic-first RTL page, warm-black + gold — the product's visual language
  (AGENTS.md §3; matches web/src themes). Single inline HTML string; the
  page is served by pywebview's internal server, no files, no network.
- Close button HIDES to the tray (the Node keeps serving); real exit is the
  tray menu or the window's إنهاء button — deliberate, a Node that dies
  because the user closed a window would strand the brain mid-task.
- pystray + Pillow are tray conveniences, imported LAZILY: without them the
  shell still runs (close = quit, honest notice in the UI), and headless
  environments can import this module for tests.
- The pure view logic (ShellState) is headless-testable; webview/pystray are
  touched only inside run_shell / build_tray_icon.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):  # direct-script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aali_node import daemon as ND  # noqa: E402

__all__ = ["ShellState", "ShellApi", "build_tray_icon", "run_shell"]

_TITLE = "عقدة آلي — Aali Node"

# Keep in sync with the daemon's documented states.
_STATE_AR = {
    "starting": "بدء التشغيل…",
    "connected": "متصل بالمركز",
    "error": "خطأ",
    "stopped": "متوقف",
}
_STATE_CLASS = {
    "starting": "wait", "connected": "run", "error": "bad", "stopped": "idle",
}

_LOG_CAP = 200


class ShellState:
    """Fold daemon status snapshots into a JSON-able Arabic view (pure)."""

    def __init__(self, log_cap: int = _LOG_CAP) -> None:
        self.log_cap = log_cap
        self._seq = 0
        self._log: list[dict[str, Any]] = []
        self._last_event: str | None = None
        self._last_stats: dict[str, int] = {}

    @property
    def log(self) -> list[dict[str, Any]]:
        return list(self._log)

    def fold(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Merge one daemon snapshot; return the full view for the page."""
        state = str(snapshot.get("state") or "starting")
        stats = snapshot.get("stats") if isinstance(snapshot.get("stats"),
                                                    dict) else {}
        stats = {k: int(v) for k, v in stats.items() if isinstance(v, int)}

        event = str(snapshot.get("last_event") or "")
        if event and event != self._last_event:
            self._last_event = event
            self._push("event", event)
        if stats != self._last_stats and "executed" in stats:
            # a single line per stats change keeps the log calm; counters
            # are always visible in the cards anyway
            self._push("stats",
                       f"تم {stats.get('executed', 0)} · رفض "
                       f"{stats.get('denied', 0)} · أخطاء {stats.get('errors', 0)}")
        self._last_stats = stats

        staged = str(snapshot.get("last_error") or "")
        if staged:
            self._push("error", staged)

        since = snapshot.get("connected_since")
        return {
            "state": state,
            "state_ar": _STATE_AR.get(state, state),
            "state_class": _STATE_CLASS.get(state, "wait"),
            "hub_url": str(snapshot.get("hub_url") or ""),
            "node_id": str(snapshot.get("node_id") or ""),
            "session_id": str(snapshot.get("session_id") or "")[:8],
            "workspace": str(snapshot.get("workspace") or ""),
            "allow_commands": bool(snapshot.get("allow_commands", True)),
            "connected_since": float(since) if since else None,
            "stats": stats,
            "log": self._log,
        }

    def _push(self, kind: str, text: str) -> None:
        self._seq += 1
        self._log.insert(0, {"seq": self._seq, "kind": kind, "text": text,
                             "t": time.strftime("%H:%M:%S")})
        del self._log[self.log_cap:]


_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>عقدة آلي</title>
<style>
  :root {
    --bg: #252523; --panel: #2d2d2a; --line: #3a3a36;
    --text: #e8e4d8; --dim: #9a958a; --gold: #d4a94e; --ok: #7fb069;
    --bad: #c96f5f; --wait: #d4a94e; --idle: #8a8a80;
  }
  * { box-sizing: border-box; margin: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: "Segoe UI", Tahoma, sans-serif; font-size: 14px;
    padding: 14px; user-select: none;
  }
  header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--idle); }
  .dot.run { background: var(--ok); } .dot.bad { background: var(--bad); }
  .dot.wait { background: var(--wait); }
  h1 { font-size: 16px; font-weight: 600; color: var(--gold); }
  .sub { color: var(--dim); font-size: 12px; }
  .cards { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 12px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
          padding: 10px; text-align: center; }
  .card b { display: block; font-size: 20px; color: var(--gold); }
  .card span { color: var(--dim); font-size: 11px; }
  .row { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
         padding: 9px 11px; margin-bottom: 8px; font-size: 12.5px; }
  .row .k { color: var(--dim); margin-inline-start: 4px; }
  .row code { direction: ltr; unicode-bidi: embed; font-size: 11.5px; color: var(--text); }
  .badge { padding: 1px 8px; border-radius: 8px; font-size: 11px; }
  .badge.on { background: #24402a; color: var(--ok); }
  .badge.off { background: #402a24; color: var(--bad); }
  #log { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
         height: 220px; overflow-y: auto; padding: 8px; }
  #log div { padding: 3px 2px; font-size: 12px; border-bottom: 1px solid #33332f; }
  #log .t { color: var(--dim); font-size: 10.5px; margin-inline-end: 6px; }
  #log .error { color: var(--bad); }
  #log .stats { color: var(--dim); }
  .btns { display: flex; gap: 8px; margin-top: 12px; }
  button { flex: 1; background: var(--panel); color: var(--text);
           border: 1px solid var(--line); border-radius: 10px; padding: 9px;
           font-family: inherit; font-size: 13px; cursor: pointer; }
  button:hover { border-color: var(--gold); color: var(--gold); }
  button.warn:hover { border-color: var(--bad); color: var(--bad); }
  .traynote { color: var(--dim); font-size: 11px; margin-top: 8px; text-align: center; }
</style>
</head>
<body>
  <header>
    <div class="dot" id="dot"></div>
    <div>
      <h1>عقدة آلي <span class="sub">Aali Node</span></h1>
      <div class="sub" id="state">…</div>
    </div>
  </header>
  <div class="cards">
    <div class="card"><b id="n-exec">0</b><span>عمليات نُفذت</span></div>
    <div class="card"><b id="n-deny">0</b><span>رفضت</span></div>
    <div class="card"><b id="n-err">0</b><span>أخطاء</span></div>
  </div>
  <div class="row">المركز <code id="hub">—</code></div>
  <div class="row">العقدة <code id="node">—</code> · الجلسة <code id="sess">—</code></div>
  <div class="row">مساحة العمل <code id="ws">—</code>
    &nbsp;<span class="badge off" id="cmds">الأوامر مغلقة</span></div>
  <div id="log"></div>
  <div class="btns">
    <button onclick="pywebview.api.open_workspace()">فتح مساحة العمل</button>
    <button class="warn" onclick="pywebview.api.quit_app()">إنهاء العقدة</button>
  </div>
  <div class="traynote" id="traynote"></div>
<script>
  const esc = s => String(s).replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  async function tick() {
    try {
      const v = await pywebview.api.snapshot();
      document.getElementById('dot').className = 'dot ' + v.state_class;
      document.getElementById('state').textContent = v.state_ar;
      document.getElementById('n-exec').textContent = v.stats.executed || 0;
      document.getElementById('n-deny').textContent = v.stats.denied || 0;
      document.getElementById('n-err').textContent = v.stats.errors || 0;
      document.getElementById('hub').textContent = v.hub_url || '—';
      document.getElementById('node').textContent = v.node_id || '—';
      document.getElementById('sess').textContent = v.session_id || '—';
      document.getElementById('ws').textContent = v.workspace || '—';
      const c = document.getElementById('cmds');
      c.textContent = v.allow_commands ? 'الأوامر مسموحة' : 'الأوامر مغلقة';
      c.className = 'badge ' + (v.allow_commands ? 'on' : 'off');
      const log = document.getElementById('log');
      log.innerHTML = v.log.map(e =>
        `<div class="${esc(e.kind)}"><span class="t">${esc(e.t)}</span>${esc(e.text)}</div>`
      ).join('');
    } catch (e) { /* window closing */ }
  }
  setInterval(tick, 1000);
  setTimeout(tick, 400);
</script>
</body>
</html>
"""


class ShellApi:
    """pywebview js_api bridge — everything the page may ask for."""

    def __init__(self, state: ShellState, status: dict[str, Any],
                 workspace_getter: Callable[[], str],
                 quit_event: threading.Event) -> None:
        self._state = state
        self._status = status
        self._workspace = workspace_getter
        self._quit = quit_event
        self._window: Any = None
        self.tray_available = False

    def bind_window(self, window: Any) -> None:
        self._window = window

    def snapshot(self) -> str:
        # pywebview 6 serializes dict returns, but a JSON string is the
        # shape every version handles — parse is trivial on the page side.
        return json.dumps(self._state.fold(dict(self._status)))

    def open_workspace(self) -> None:
        path = self._workspace()
        if not path:
            return
        try:
            if sys.platform == "win32":
                import os
                os.startfile(path)  # noqa: S606 — user's own workspace
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass  # a failed explorer open must never touch the daemon

    def quit_app(self) -> None:
        self._quit.set()
        if self._window is not None:
            try:
                self._window.show()
                self._window.destroy()
            except Exception:
                pass


def build_tray_icon(title: str, workspace: str,
                    on_show: Callable[[], None],
                    on_quit: Callable[[], None]) -> Any | None:
    """Build + detach the tray icon; None when pystray/Pillow are missing.

    Runs in ANY thread (run_detached); the icon is a small gold-on-dark
    mark drawn with Pillow (no font dependency, no external asset).
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(37, 37, 35, 255),
                        outline=(212, 169, 78, 255), width=3)
    d.ellipse([24, 24, 40, 40], fill=(212, 169, 78, 255))

    menu = pystray.Menu(
        pystray.MenuItem("إظهار / Show", lambda *_: on_show(), default=True),
        pystray.MenuItem("إنهاء العقدة / Quit", lambda *_: on_quit()),
    )
    icon = pystray.Icon("aali_node", img, title, menu)
    icon.run_detached(setup=lambda i: setattr(i, "visible", True))
    return icon


def run_shell(hub_url: str, token: str, workspace_root: str | Path, *,
              allow_commands: bool = True,
              update_hub: str | None = None,
              update_key: str | None = None,
              install_root: "str | Path | None" = None) -> int:
    """GUI entry: daemon thread + window + tray. Blocks until exit."""
    if not token:
        print("aali_node shell: no token — pass --token or set "
              "AALI_NODE_TOKEN", file=sys.stderr)
        return 2

    try:
        import webview  # noqa: F401 — fail BEFORE spawning the daemon thread
    except ImportError:
        print("aali_node shell: pywebview is not installed in this venv "
              "(pip install pywebview>=5.0)", file=sys.stderr)
        return 2

    workspace = str(Path(workspace_root))
    status: dict[str, Any] = {}
    stop = threading.Event()
    quit_event = threading.Event()
    rc_holder: list[int] = [-1]

    def _daemon() -> None:
        rc_holder[0] = ND.run_node(
            hub_url, token, workspace_root,
            allow_commands=allow_commands, native_confirm=True,
            update_hub=update_hub, update_key=update_key,
            status=status, stop=stop, install_root=install_root)

    node_thread = threading.Thread(target=_daemon, daemon=True,
                                   name="aali-node-daemon")
    node_thread.start()

    state = ShellState()
    api = ShellApi(state, status, lambda: status.get("workspace", workspace),
                   quit_event)
    window = webview.create_window(
        _TITLE, html=_PAGE, js_api=api, width=480, height=680,
        min_size=(400, 520), background_color="#252523")
    api.bind_window(window)

    def _hide_to_tray() -> bool:
        # Cancelable pywebview event: returning False cancels the close.
        # Hide only while the daemon is alive — once it stopped, closing
        # the window means closing the app (no orphaned hidden window).
        if api.tray_available and node_thread.is_alive():
            window.hide()
            return False
        return True

    window.events.closing += _hide_to_tray

    def _watch() -> None:
        tray = None
        try:
            tray = build_tray_icon(
                _TITLE, workspace,
                on_show=lambda: window.show(),
                on_quit=lambda: quit_event.set())
        except Exception:
            tray = None
        api.tray_available = tray is not None
        while not quit_event.wait(timeout=0.5):
            if not node_thread.is_alive():
                break  # daemon died on its own — surface it, allow close
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        if quit_event.is_set():
            # tray Quit: end the daemon, then the window — webview.start
            # only returns when every window is destroyed, and a hidden
            # window still counts.
            stop.set()
            if node_thread.is_alive():
                node_thread.join(timeout=3.0)
            try:
                window.show()
                window.destroy()
            except Exception:
                pass

    try:
        webview.start(_watch)
    except Exception as exc:
        # No desktop / GUI backend: fall back honestly, keep serving.
        print(f"aali_node shell: GUI unavailable ({type(exc).__name__}); "
              "the daemon keeps running headless — Ctrl+C to stop",
              file=sys.stderr)
        try:
            node_thread.join()
        except KeyboardInterrupt:
            stop.set()
            node_thread.join(timeout=3.0)
        return rc_holder[0]

    return rc_holder[0]
