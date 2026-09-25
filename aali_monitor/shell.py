"""Aali Monitor shell — pywebview window + pystray tray (2026-09-25).

Same proven mechanics as aali_node/shell.py (same dependency set, already
debugged there): a pywebview window over a poller thread, a tray icon,
and an honest headless fallback when no GUI backend exists. The poller
calls aali_monitor.core.snapshot() and fires a tray notification when a
NEW alert line appears (dedup: the same alert does not re-fire every
poll; it re-arms once the condition clears).

Arabic-first UI. Content-free: only state, numbers and alert lines are
shown — never chat or file content.
"""
from __future__ import annotations

import json
import sys
import threading
from typing import Any

from . import core

_TITLE = "آلي Monitor — مراقب أعمال آلي"
_POLL_SECONDS = 20


_PAGE = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>آلي Monitor</title>
<style>
  :root { --bg:#252523; --panel:#2d2d2a; --line:#3a3a36;
          --fg:#f0ead8; --mut:#9a958a; --gold:#d4a94e; --bad:#e74c3c; --ok:#2ecc71; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, 'Tajawal', sans-serif;
         background:var(--bg); color:var(--fg); }
  header { padding:14px 18px; border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:10px; }
  header h1 { font-size:16px; margin:0; }
  .dot { width:10px; height:10px; border-radius:50%; background:var(--mut); }
  .dot.ok { background:var(--ok); } .dot.bad { background:var(--bad); }
  main { padding:14px 18px; display:grid; gap:10px; }
  .row { background:var(--panel); border:1px solid var(--line);
         border-radius:10px; padding:10px 14px; display:flex;
         justify-content:space-between; gap:10px; font-size:14px; }
  .row b { color:var(--gold); font-weight:600; }
  .muted { color:var(--mut); font-size:12px; }
  #alerts { display:grid; gap:6px; }
  .alert { background:rgba(231,76,60,.12); border:1px solid rgba(231,76,60,.4);
           border-radius:10px; padding:8px 12px; font-size:13px; }
  .quiet { color:var(--mut); font-size:13px; padding:6px 2px; }
</style>
</head>
<body>
<header><span class="dot" id="dot"></span><h1>آلي Monitor</h1>
  <span class="muted" id="ts">—</span></header>
<main>
  <div class="row"><span>واجهة آلي :5055</span><b id="api">—</b></div>
  <div class="row"><span>العقل :20129</span><b id="brain">—</b></div>
  <div class="row"><span>كرت الشاشة</span><b id="gpu">—</b></div>
  <div class="row"><span>المدرّب (Phase D)</span><b id="trainer">—</b></div>
  <div class="row"><span>خط التخرّج</span><b id="pipeline">—</b></div>
  <div class="row"><span>مساحة D:</span><b id="disk">—</b></div>
  <div id="alerts"></div>
  <div class="quiet">التنبيهات تُقرأ من سجلات D:/hwk-data الحقيقية — بدون أي محتوى محادثات.</div>
</main>
<script>
  const esc = s => String(s).replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  async function tick() {
    try {
      const v = JSON.parse(await pywebview.api.snapshot());
      document.getElementById('ts').textContent = v.ts || '';
      document.getElementById('dot').className = 'dot ' + (v.alert_lines.length ? 'bad' : 'ok');
      const put = (id, txt, bad) => {
        const el = document.getElementById(id);
        el.textContent = txt;
        el.style.color = bad ? 'var(--bad)' : '';
      };
      put('api',    v.api_up ? 'مستجيبة ✓' : 'لا يستجيب ✕', !v.api_up);
      put('brain',  v.brain_up ? 'مستجيب ✓' : 'لا يستجيب ✕', !v.brain_up);
      put('gpu',    v.gpu ? (v.gpu.name + ' — ' + v.gpu.util_pct + '% — ' +
            (v.gpu.vram_used_mib/1024).toFixed(1) + '/' +
            (v.gpu.vram_total_mib/1024).toFixed(1) + ' GB — ' + v.gpu.temp_c + '°C')
            : 'لا توجد بطاقة');
      const tr = v.trainer || {};
      put('trainer', tr.state === 'running' ? ('خطوة ' + tr.step + '/' + tr.total)
            : tr.state === 'done' ? 'منتهٍ ✓' : tr.state);
      const pv = (v.pipeline || {}).verdict;
      put('pipeline', pv === 'promote' ? 'PROMOTE ✓' : pv === 'no-go' ? 'NO-GO ✕'
            : (pv ? pv : 'لا يوجد'));
      put('disk', v.disk_free_gb != null ? (v.disk_free_gb + ' GB حرّة') : '—',
          v.disk_free_gb != null && v.disk_free_gb < 20);
      const box = document.getElementById('alerts');
      box.innerHTML = v.alert_lines.length
        ? v.alert_lines.map(a => '<div class="alert">' + esc(a) + '</div>').join('')
        : '<div class="quiet">كل شيء هادئ ✦</div>';
    } catch (e) { /* window closing */ }
  }
  setInterval(tick, 2000);
  setTimeout(tick, 300);
</script>
</body>
</html>
"""


class MonitorApi:
    """pywebview js_api bridge — the page may only read snapshots."""

    def __init__(self, state: dict[str, Any], quit_event: threading.Event) -> None:
        self._state = state
        self._quit = quit_event
        self._window: Any = None
        self.tray_available = False

    def bind_window(self, window: Any) -> None:
        self._window = window

    def snapshot(self) -> str:
        # JSON string: the shape every pywebview version serializes safely.
        return json.dumps(self._state, ensure_ascii=False, default=str)

    def quit_app(self) -> None:
        self._quit.set()


def build_tray_icon(on_show, on_quit) -> Any | None:
    """Tray icon; None when pystray/Pillow are missing (honest degrade)."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(37, 37, 35, 255),
                        outline=(212, 169, 78, 255), width=3)
    # Eye mark: the monitor WATCHES.
    d.ellipse([16, 24, 48, 40], outline=(212, 169, 78, 255), width=3)
    d.ellipse([27, 28, 37, 36], fill=(212, 169, 78, 255))

    menu = pystray.Menu(
        pystray.MenuItem("إظهار / Show", lambda *_: on_show(), default=True),
        pystray.MenuItem("إنهاء المراقب / Quit", lambda *_: on_quit()),
    )
    icon = pystray.Icon("aali_monitor", img, _TITLE, menu)
    icon.run_detached(setup=lambda i: setattr(i, "visible", True))
    return icon


def notify(icon: Any | None, title: str, message: str) -> None:
    """Tray notification when a backend exists; console otherwise."""
    if icon is not None:
        try:
            icon.notify(message, title)
            return
        except Exception:
            pass
    try:
        print(f"[monitor] {title}: {message}", file=sys.stderr)
    except OSError:
        pass


def poll_loop(state: dict[str, Any], quit_event: threading.Event,
              icon_getter, *, snapshot_fn=None,
              max_polls: int | None = None) -> None:
    """Background poller: refresh the snapshot, notify on NEW alerts.

    snapshot_fn/max_polls are injection seams for tests: the suite passes
    a stub snapshot and a poll count so it never touches the live logs,
    GPU or network. Production uses the defaults (real snapshot, forever)."""
    snapshot_fn = snapshot_fn or core.snapshot
    seen: set[str] = set()
    polls = 0
    while not quit_event.wait(timeout=_POLL_SECONDS):
        polls += 1
        try:
            snap = snapshot_fn()
        except Exception as exc:  # noqa: BLE001 — monitor must never die
            state["alert_lines"] = [f"تعذّر جمع الحالة: {type(exc).__name__}"]
            continue
        state.clear()
        state.update(snap)
        current = set(snap.get("alert_lines") or [])
        for line in sorted(current - seen):
            notify(icon_getter(), "آلي Monitor", line)
        seen = current  # re-arms automatically when the alert clears
        if max_polls is not None and polls >= max_polls:
            return


def run_monitor(*, poll_seconds: int = _POLL_SECONDS,
                headless: bool = False,
                snapshot_fn=None, max_polls: int | None = None) -> int:
    """GUI entry: poller thread + window + tray. Blocks until exit.
    snapshot_fn/max_polls: test seams (see poll_loop)."""
    global _POLL_SECONDS
    _POLL_SECONDS = max(5, poll_seconds)

    state: dict[str, Any] = {}
    quit_event = threading.Event()
    api = MonitorApi(state, quit_event)
    icon_holder: list[Any] = [None]

    poller = threading.Thread(
        target=poll_loop,
        args=(state, quit_event, lambda: icon_holder[0]),
        kwargs={"snapshot_fn": snapshot_fn, "max_polls": max_polls},
        daemon=True, name="aali-monitor-poller")
    poller.start()

    if headless:
        try:
            print("aali-monitor: headless mode — alerts on stderr; "
                  "Ctrl+C to stop", file=sys.stderr)
            poller.join()
        except KeyboardInterrupt:
            quit_event.set()
        return 0

    try:
        import webview  # fail BEFORE going further (aali_node lesson)
    except ImportError:
        print("aali-monitor: pywebview is not installed in this venv — "
              "run from .venv-monitor or use --headless", file=sys.stderr)
        quit_event.set()
        return 2

    window = webview.create_window(
        _TITLE, html=_PAGE, js_api=api, width=460, height=640,
        min_size=(400, 520), background_color="#252523")
    api.bind_window(window)

    def _hide_to_tray() -> bool:
        # Same contract as aali_node: a window close must never kill the
        # watcher silently — hide to tray while it is running.
        if api.tray_available and not quit_event.is_set():
            window.hide()
            return False
        return True

    window.events.closing += _hide_to_tray

    def _watch() -> None:
        tray = None
        try:
            tray = build_tray_icon(
                on_show=lambda: window.show(),
                on_quit=lambda: quit_event.set())
        except Exception:
            tray = None
        icon_holder[0] = tray
        api.tray_available = tray is not None

    try:
        webview.start(_watch)
    except Exception as exc:
        print(f"aali-monitor: GUI unavailable ({type(exc).__name__}); "
              "the poller keeps running headless — Ctrl+C to stop",
              file=sys.stderr)
        try:
            poller.join()
        except KeyboardInterrupt:
            quit_event.set()
        return 0

    quit_event.set()
    return 0
