"""آلي Desktop — native window shell around the web client (pywebview + WebView2).

Same brain, dedicated window: start the server with scripts\\start_all.bat,
then run:  .venv/Scripts/python desktop_app.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import webview

WEB_DIR = Path(__file__).resolve().parent / "web"
SERVER = os.getenv("AALI_SERVER", "http://127.0.0.1:5055")


def _server_ready() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(SERVER + "/api/health", timeout=4) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def _wait_for_server(seconds: int = 20) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _server_ready():
            return True
        time.sleep(1)
    return False


def _inject_api_base(window) -> None:
    """Point the web client at the local server and register the SW origin."""
    try:
        window.evaluate_js(
            "localStorage.setItem('aali_api', " + f"'{SERVER}'" + ");"
            "if (window.aaliPing) window.aaliPing();"
        )
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    if not _server_ready():
        print(f"[آلي] الخادم غير مستجيب على {SERVER} — شغّل scripts\\start_all.bat أولاً")
        print("[آلي] سأفتح النافذة على أي حال؛ يمكنك ضبط العنوان من ⚙︎ داخل التطبيق")

    window = webview.create_window(
        "آلي — Desktop",
        str(WEB_DIR / "index.html"),
        width=1180,
        height=820,
        min_size=(760, 560),
        background_color="#0b0e14",
    )
    webview.start(_inject_api_base, window, private_mode=False,
                  storage_path="D:/hwk-data/webview_profile")


if __name__ == "__main__":
    main()
