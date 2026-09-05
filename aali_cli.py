"""آلي CLI — terminal client for the local Aali brain (same API as web/iOS).

Usage:
  python aali_cli.py                      # chat with the default server
  python aali_cli.py --server http://192.168.1.10:5055
  python aali_cli.py "سؤالك المباشر"      # one-shot: ask and exit

Requires the app server:  scripts\\start_all.bat   (or PORT=5055 python file-agent/app.py)
Optional prettiness:  pip install rich   (falls back to plain output)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API_PATH_ASK = "/api/ask"
API_PATH_HEALTH = "/api/health"

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    console: Console | None = Console()
except ImportError:  # plain fallback, zero dependencies
    console = None

SID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".aali_cli_sid")


def _load_sid() -> str:
    try:
        with open(SID_FILE, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _save_sid(sid: str) -> None:
    try:
        with open(SID_FILE, "w", encoding="utf-8") as handle:
            handle.write(sid)
    except OSError:
        pass


def _post(server: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        server.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Session-Id": _load_sid()},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def _health(server: str) -> bool:
    try:
        with urllib.request.urlopen(server.rstrip("/") + API_PATH_HEALTH, timeout=5) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def say(text: str) -> None:
    if console is not None:
        console.print(Panel(text, title="آلي", border_style="gold3", padding=(0, 1)))
    else:
        print(f"\nآلي:\n{text}\n")


def err(text: str) -> None:
    if console is not None:
        console.print(f"[red]{text}[/red]")
    else:
        print(text, file=sys.stderr)


def one_shot(server: str, message: str) -> int:
    try:
        data = _post(server, API_PATH_ASK, {"message": message, "sid": _load_sid() or None})
    except urllib.error.URLError as exc:
        err(f"تعذّر الاتصال بالخادم {server}: {exc}")
        return 2
    if data.get("sid"):
        _save_sid(str(data["sid"]))
    say(str(data.get("reply") or data.get("error") or ""))
    return 0 if data.get("ok") else 1


def repl(server: str) -> int:
    if not _health(server):
        err(f"الخادم غير مستجيب على {server} — شغّل scripts\\start_all.bat أولاً")
        return 2
    if console is not None:
        console.print("[bold gold3]آلي[/] — مساعدك في الطرفية. اكتب سؤالك، أو /new لمحادثة جديدة، /quit للخروج.")
    else:
        print("آلي — اكتب سؤالك، /new لمحادثة جديدة، /quit للخروج")
    while True:
        try:
            line = input("\nأنت> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            return 0
        if line == "/new":
            _save_sid("")
            if console is not None:
                console.print("[gold3]— محادثة جديدة —[/gold3]")
            else:
                print("— محادثة جديدة —")
            continue
        try:
            data = _post(server, API_PATH_ASK, {"message": line, "sid": _load_sid() or None})
        except urllib.error.URLError as exc:
            err(f"خطأ اتصال: {exc}")
            continue
        if data.get("sid"):
            _save_sid(str(data["sid"]))
        say(str(data.get("reply") or data.get("error") or ""))


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="آلي CLI — terminal client for the local brain")
    parser.add_argument("message", nargs="?", default=None, help="سؤال مباشر (one-shot)")
    parser.add_argument("--server", default=os.getenv("AALI_SERVER", "http://127.0.0.1:5055"))
    args = parser.parse_args()
    if args.message:
        raise SystemExit(one_shot(args.server, args.message))
    raise SystemExit(repl(args.server))


if __name__ == "__main__":
    main()
