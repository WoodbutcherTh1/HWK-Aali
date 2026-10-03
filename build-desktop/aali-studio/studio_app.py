"""آلي ستوديو — desktop window (pywebview/WebView2) around the Studio server.

One window, one Flask server on 127.0.0.1:5070, no install and no cloud: the
IDE works with the machine's own brain (HWK-AZiZA on :5055) and, if the owner
pastes a key, with API models too.

    python studio_app.py                 # window on the default workspace
    python studio_app.py --port 5071     # another port
    python studio_app.py --workspace D:/my-project
    python studio_app.py --server-only   # headless (tests, remote boxes)

Studio is named after Azeza (عزيزة), the owner's grandmother.
"""

from __future__ import annotations

# The Arabic --help must print on every console, including a cp1252 Windows
# pipe: without this the entry-point dies inside argparse itself
# (UnicodeEncodeError before main() ever runs).
import sys as _sys

for _stream_name in ("stdout", "stderr"):
    try:
        _stream = getattr(_sys, _stream_name)
        if _stream.encoding and _stream.encoding.lower().replace("-", "") != "utf8":
            import io as _io
            setattr(_sys, _stream_name,
                    _io.TextIOWrapper(_stream.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True))
    except Exception:  # noqa: BLE001 - a console quirk must not stop boot
        pass

import argparse
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Entry-point tripwire: bootstrap sys.path ourselves (frozen exe, bare
# checkout, no PYTHONPATH) — same rule every script in this repo follows.
_REPO = HERE.parents[1]
for _extra in (_REPO / "file-agent", HERE):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import models_proxy as mp  # noqa: E402
import studio_server  # noqa: E402

APP_TITLE = "آلي ستوديو — Aali Studio"
WINDOW_WIDTH, WINDOW_HEIGHT = 1500, 940


def _start_server(port: int) -> None:
    thread = threading.Thread(
        target=studio_server.create_app().run,
        kwargs={"host": studio_server.HOST, "port": port,
                "debug": False, "threaded": True, "use_reloader": False},
        name="aali-studio-server", daemon=True)
    thread.start()


def _wait_ready(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.25)
    return False


def _pick_workspace(argument: str | None) -> str:
    """--workspace wins; otherwise keep whatever Studio was last opened on."""
    if argument:
        path = Path(argument).expanduser()
        if path.is_dir():
            mp.save_settings({"workspace": str(path.resolve())})
        else:
            print(f"[studio] workspace not found, keeping the last one: {argument}")
    return str(mp.settings().get("workspace") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="آلي ستوديو — Aali Studio")
    parser.add_argument("--port", type=int, default=studio_server.DEFAULT_PORT)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--server-only", action="store_true",
                        help="run the backend with no window")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    if args.version:
        print(f"Aali Studio {studio_server.ABOUT['version']}")
        return 0

    workspace = _pick_workspace(args.workspace)
    _start_server(args.port)
    if not _wait_ready(args.port):
        print(f"[studio] the server did not come up on :{args.port}")
        return 1
    # UI preferences live server-side (settings.json), so the window, the
    # browser and a future second window all agree on the owner's choices.
    ui = _ui_prefs()
    url = f"http://127.0.0.1:{args.port}/?fs={ui['font_size']}&wrap={ui['word_wrap']}&term={ui['terminal_visible']}"
    print(f"[studio] workspace: {workspace or '(home)'}")
    print(f"[studio] open: {url}")

    if args.server_only:
        _idle()
        return 0

    opened = False
    try:
        import webview
    except ImportError:
        print("[studio] pywebview is not installed — running as a browser app; "
              "open the printed URL (pip install pywebview for the window)")
    else:
        try:
            webview.create_window(
                APP_TITLE, url, js_api=None,
                width=WINDOW_WIDTH, height=WINDOW_HEIGHT, min_size=(1100, 700))
            webview.start(debug=False)
            opened = True
        except Exception as exc:  # noqa: BLE001
            # The exe is BUILT WITHOUT a console, so an exception here would be
            # invisible: no window, no message, nothing. Fall back to the
            # browser and say why (AGENTS.md: a green build can still die).
            print(f"[studio] the window could not open ({exc}) — "
                  f"falling back to the browser at {url}")
    if not opened:
        import webbrowser
        webbrowser.open(url)
        _idle()
    return 0


def _ui_prefs() -> dict[str, str]:
    """The owner's UI preferences from settings.json, already validated."""
    try:
        s = mp.settings()
    except Exception:  # noqa: BLE001 - prefs must never block the window
        s = {}
    font = str(s.get("font_size") or "14")
    if not font.isdigit() or not 11 <= int(font) <= 24:
        font = "14"
    wrap = "off" if str(s.get("word_wrap")) == "off" else "on"
    term = "0" if str(s.get("terminal_visible")) == "0" else "1"
    return {"font_size": font, "word_wrap": wrap, "terminal_visible": term}


def _idle() -> None:
    """Stay alive until the owner closes the app (window close = process end)."""
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    raise SystemExit(main())
