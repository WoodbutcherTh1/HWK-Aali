# -*- coding: utf-8 -*-
"""Aali CLI — a Claude Code-style terminal client for the Aali server.

Colorful, animated, Linux-terminal look: banner box, gold prompt, live spinner
while Aali thinks, tool lines streaming as he works (like Claude Code's tool
traces), markdown-ish rendering, follow-up suggestion chips, slash commands.

Usage:
    .venv\\Scripts\\python.exe scripts\\aali_cli.py              # interactive
    .venv\\Scripts\\python.exe scripts\\aali_cli.py -q "2+2?"    # one-shot
    .venv\\Scripts\\python.exe scripts\\aali_cli.py --markdown   # pipe file
    .venv\\Scripts\\python.exe scripts\\aali_cli.py --theme matrix
    .venv\\Scripts\\python.exe scripts\\aali_cli.py --base http://127.0.0.1:5055

Keys: Ctrl+C cancels the current request (or exits on the prompt);
      /exit quits, /new starts a fresh session, /open N resumes,
      /clear clears the screen, /theme switches the colour theme,
      /help lists commands.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Windows consoles/pipes default to cp1252 — force UTF-8 so Arabic and box
# glyphs always survive (esp. inside the PyInstaller exe).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

try:
    import colorama

    colorama.just_fix_windows_console()
    _ = colorama  # noqa: F841
except Exception:  # noqa: BLE001
    pass

# ----------------------------------------------------------------- palette
# Role-based themes: every paint(…, "gold") call really means "primary
# accent" — each theme decides which colour that is, so switching themes
# never touches a call site.
THEMES: dict[str, dict[str, str]] = {
    "gold": {  # the warm HWK brand look (default)
        "gold": "38;5;179",
        "green": "38;5;114",
        "cyan": "38;5;80",
        "red": "38;5;203",
        "grey": "38;5;245",
        "white": "97",
    },
    "matrix": {  # phosphor terminal green on black
        "gold": "38;5;118",
        "green": "38;5;46",
        "cyan": "38;5;50",
        "red": "38;5;196",
        "grey": "38;5;250",
        "white": "97",
    },
    "ocean": {  # deep-sea blues and teal
        "gold": "38;5;81",
        "green": "38;5;79",
        "cyan": "38;5;117",
        "red": "38;5;210",
        "grey": "38;5;245",
        "white": "97",
    },
}
DEFAULT_THEME = "gold"
THEME_FILE = Path.home() / ".aali_cli_theme"
theme_name = DEFAULT_THEME

C = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def set_theme(name: str) -> bool:
    """Activate a theme by (fuzzy) name; repaint the global palette C."""
    global theme_name
    wanted = name.strip().lower()
    match = None
    for key in THEMES:
        if wanted == key or key.startswith(wanted) or wanted.startswith(key):
            match = key
            break
    if match is None:
        return False
    theme_name = match
    for role, code in THEMES[match].items():
        C[role] = f"\033[{code}m"
    return True


def load_saved_theme() -> str:
    """Apply the theme saved by /theme (defaults to gold when none/invalid)."""
    try:
        saved = THEME_FILE.read_text(encoding="utf-8").strip().lower()
        if saved in THEMES:
            set_theme(saved)
            return saved
    except OSError:
        pass
    set_theme(DEFAULT_THEME)
    return DEFAULT_THEME


def save_theme(name: str) -> None:
    """Persist the chosen theme across sessions (best effort)."""
    try:
        THEME_FILE.write_text(name + "\n", encoding="utf-8")
    except OSError:
        pass


set_theme(DEFAULT_THEME)

# BRB spinner — the "moving" element (straight ASCII, terminal-safe)
SPINNER = ["|", "/", "-", "\\"]


def _supports_color() -> bool:
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(text: str, *styles: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    return "".join(C.get(s, "") for s in styles) + text + C["reset"]


# ----------------------------------------------------------------- helpers
def http_json(base: str, path: str, timeout: float = 10) -> dict:
    req = urllib.request.Request(base + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_server(base: str, timeout: float = 60.0) -> None:
    """Health-check the Aali server; boot it via start_app.bat if absent."""
    try:
        http_json(base, "/api/health", timeout=3)
        return
    except Exception:  # noqa: BLE001
        pass
    print(paint("✻ Aali is not running — starting the server…", "cyan"))
    root_str = str(ROOT)
    if os.name == "nt":
        subprocess.Popen(
            ["cmd", "/c", "start", "/min", "scripts\\start_app.bat"],
            cwd=root_str, creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    else:
        subprocess.Popen(["bash", str(ROOT / "scripts" / "start_app.sh")], cwd=root_str)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        try:
            http_json(base, "/api/health", timeout=2)
            print(paint("✻ Server is up.", "green"))
            return
        except Exception:  # noqa: BLE001
            continue
    print(paint(f"✗ Could not reach the Aali server at {base}", "red"))
    sys.exit(1)


# ----------------------------------------------------------------- banner
def banner(width: int) -> None:
    if width < 46:
        return
    inner = 38
    g, d, r = C["gold"], C["dim"], C["reset"]
    l1 = "  AALI · agent on your machine".ljust(inner)
    l2 = f"  local · private · own model · {theme_name}".ljust(inner)
    print()
    print(f"{g}╭{'─' * inner}╮{r}")
    print(f"{g}│{r}{l1}{g}│{r}")
    print(f"{g}│{r}{d}{l2}{r}{g}│{r}")
    print(f"{g}╰{'─' * inner}╯{r}")
    print(paint("  آلي — مساعدك المحلي، جاهز.", "gold"))
    print(paint("  /help commands · Ctrl+C cancel · /exit quit", "dim"))


# ----------------------------------------------------------------- markdown-ish
_MD = [
    (re.compile(r"^### (.*)$"), "h"),
    (re.compile(r"^## (.*)$"), "h"),
    (re.compile(r"^# (.*)$"), "h"),
    (re.compile(r"^\s*[-*•] (.*)$"), "li"),
    (re.compile(r"^\s*(\d+)[.)] (.*)$"), "oli"),
    (re.compile(r"^```"), "fence"),
]


def render_reply(text: str, color: bool) -> None:
    """Light markdown-ish rendering: headings, bullets, code, inline styles."""
    in_fence = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_fence = not in_fence
            rule = "─" * 28
            print(paint(rule if in_fence else rule, "dim", enabled=color))
            continue
        if in_fence:
            print(paint(line, "cyan", enabled=color))
            continue
        for pat, kind in _MD:
            m = pat.match(line)
            if not m:
                continue
            body = m.group(1)
            if kind == "h":
                print(paint(body, "bold", "gold", enabled=color))
            elif kind == "li":
                print(paint("  • ", "green", enabled=color) + body)
            elif kind == "oli":
                print(paint(f"  {m.group(1)}. ", "green", enabled=color) + m.group(2))
            break
        else:
            out = line
            out = re.sub(r"\*\*(.+?)\*\*", lambda mm: mm.group(1), out)
            out = re.sub(r"`([^`]+)`", lambda mm: f"{mm.group(1)}", out)
            print(out)


# ----------------------------------------------------------------- streaming
def stream_ask(base: str, message: str, sid: str, confirm: bool = False,
               api_key: str = "") -> dict:
    """POST /api/ask/stream and consume the SSE events with a live spinner.

    Activity events render as Claude Code-style tool traces; the `done`
    event carries the reply. Returns the done body ({} on cancellation).
    """
    if not sys.stdout.isatty():
        print(message)
    payload = json.dumps(
        {"message": message, "sid": sid, "confirm": confirm}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(base + "/api/ask/stream", data=payload, headers=headers)
    result: dict = {}
    frames = iter(SPINNER)
    last = 0.0
    printed_tool = False
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            ev_name = ""
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event:"):
                    ev_name = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev_name == "activity":
                    printed_tool = True
                    kind, tool = data.get("event"), str(data.get("tool", ""))
                    if kind == "provider_selected":
                        tag = paint("●", "green") + paint(
                            f" brain → {data.get('model', data.get('provider', ''))}", "grey"
                        )
                        print("\r" + " " * 60 + "\r" + tag)
                        continue
                    if kind == "tool_requested":
                        args = data.get("arguments") or {}
                        brief = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:2])
                        print("\r" + " " * 60 + "\r"
                              + paint("● ", "green") + paint(tool, "bold")
                              + paint(f"({brief})", "grey"))
                    elif kind == "tool_result":
                        res = str(data.get("result", ""))
                        bad = res.startswith("{") and ('"ok": false' in res or "'ok': False" in res)
                        mark = paint("  └─ ✗", "red") if bad else paint("  └─", "dim")
                        print(mark + paint(f" {res[:120]}", "grey"))
                elif ev_name == "done":
                    if isinstance(data, dict):
                        result = data
                    break
                now = time.time()
                if sys.stdout.isatty() and now - last > 0.12:
                    print("\r" + paint(next(frames) + " ", "gold")
                          + paint("Aali is working…", "dim"), end="", flush=True)
                    last = now
    except KeyboardInterrupt:
        print("\r" + " " * 60 + "\r" + paint("✻ cancelled — Aali keeps working server-side", "cyan"))
        return {}
    if printed_tool:
        pass
    elif sys.stdout.isatty():
        print("\r" + " " * 60 + "\r", end="")
    return result


# ----------------------------------------------------------------- one-shot
def one_shot(base: str, question: str, api_key: str = "") -> None:
    ensure_server(base)
    data = http_json(base, "/api/sessions") or {}
    sessions = (data or {}).get("sessions") or []
    sid = sessions[0]["sid"] if sessions else "cli-" + str(int(time.time()))
    result = stream_ask(base, question, sid, api_key=api_key)
    reply = str(result.get("reply", ""))
    color = _supports_color()
    print(paint("● ", "gold", enabled=color) + paint("آلي", "bold", enabled=color))
    render_reply(reply, color)
    if not result.get("ok", True):
        sys.exit(1)


# ----------------------------------------------------------------- REPL
SLASH = "/\\/_-"  # harmless constant for lint friendliness


def repl(base: str, api_key: str = "") -> None:
    ensure_server(base)
    color = _supports_color()
    width = shutil.get_terminal_size((100, 24)).columns
    banner(width)
    sid = "cli-" + str(int(time.time()))
    last_suggestions: list[str] = []
    cols = {"user": "gold", "aali": "green", "info": "cyan", "warn": "red"}

    def say(tag: str, text: str, style: str) -> None:
        print(paint(f"{tag} ", style, "bold", enabled=color) + text)

    while True:
        try:
            prompt = paint("❯ ", "gold", enabled=color)
            try:
                message = input(prompt).strip()
            except EOFError:
                break
            if not message:
                continue
            if message.startswith("/"):
                cmd, _, arg = message.partition(" ")
                cmd = cmd.lower()
                if cmd in ("/exit", "/quit", "/q"):
                    say("●", "مع السلامة — إلى اللقاء!", "info")
                    break
                if cmd == "/help":
                    print(paint("  /new new session · /open N resume session N · /clear clear screen", "dim"))
                    print(paint("  /theme switch colours (gold · matrix · ocean) — /theme alone previews", "dim"))
                    print(paint("  /sid show session id · /exit quit · Ctrl+C cancel current run", "dim"))
                    continue
                if cmd == "/theme":
                    if arg.strip():
                        if set_theme(arg):
                            save_theme(theme_name)
                            say("✻", f"theme → {theme_name} (saved)", "gold")
                            os.system("cls" if os.name == "nt" else "clear")
                            banner(width)
                        else:
                            say("✗", f"unknown theme “{arg.strip()}” — /theme lists them", "warn")
                    else:
                        print(paint("  themes:", "dim", enabled=color))
                        for key in THEMES:
                            sw = "".join(
                                paint("●", role, enabled=color)
                                for role in ("gold", "green", "cyan", "red")
                            )
                            cur = paint("  ● current", "grey", enabled=color) if key == theme_name else ""
                            print(paint(f"  {sw} ", "bold", enabled=color)
                                  + f"{key:<8}" + cur)
                        print(paint("  switch with /theme gold | matrix | ocean", "dim", enabled=color))
                    continue
                if cmd == "/new":
                    sid = "cli-" + str(int(time.time()))
                    last_suggestions = []
                    say("✻", f"fresh session {sid}", "info")
                    continue
                if cmd == "/sid":
                    say("✻", sid, "info")
                    continue
                if cmd == "/clear":
                    os.system("cls" if os.name == "nt" else "clear")
                    banner(width)
                    continue
                if cmd == "/open":
                    try:
                        idx = int(arg.strip())
                    except ValueError:
                        say("✗", "/open N — استعمل رقماً من /open 0", "warn")
                        continue
                    try:
                        data = http_json(base, "/api/sessions")
                        sessions = data.get("sessions", [])
                        if idx >= len(sessions):
                            say("✗", f"لا يوجد رقم {idx} — جرّب 0..{len(sessions) - 1}", "warn")
                            continue
                        sid = sessions[idx]["sid"]
                        hist = http_json(base, f"/api/session/{sid}")
                        say("✻", f"opened “{sessions[idx].get('title', sid)[:48]}”", "info")
                        for turn in hist.get("turns", [])[-6:]:
                            role = turn.get("role", "")
                            content = str(turn.get("content", ""))
                            who = "you" if role == "user" else "آلي"
                            style = cols["user"] if role == "user" else cols["aali"]
                            print(paint(f"{who} ", style, "bold", enabled=color)
                                  + content[:400].replace("\n", " "))
                    except Exception as exc:  # noqa: BLE001
                        say("✗", f"session fetch failed: {exc}", "warn")
                    continue
                say("✗", f"أمر غير معروف: {cmd} — جرّب /help", "warn")
                continue

            # plain chat turn
            result = stream_ask(base, message, sid, api_key=api_key)
            if not result:
                continue
            reply = str(result.get("reply", ""))
            if result.get("needs_confirm"):
                act = result.get("pending_action") or {}
                print(paint("⚠ ", "red", enabled=color) + paint(
                    f"آلي يطلب إذناً بتنفيذ: {act.get('tool')} {act.get('arguments', {})}",
                    "bold", enabled=color))
                ans = input(paint("   allow? [y/N] ", "gold", enabled=color)).strip().lower()
                if ans == "y":
                    result = stream_ask(base, message, sid, confirm=True, api_key=api_key)
                    reply = str(result.get("reply", ""))
            print()
            print(paint("● ", "gold", enabled=color) + paint("آلي", "bold", "green", enabled=color))
            render_reply(reply, color)
            suggestions = result.get("suggestions") or []
            if suggestions:
                last_suggestions = [str(s) for s in suggestions][:3]
                print()
                for i, s in enumerate(last_suggestions):
                    print(paint(f"  [{i + 1}] ", "gold", enabled=color)
                          + paint(s, "cyan", enabled=color))
            print()
        except KeyboardInterrupt:
            print(paint("\n✻ (مرتين = خروج) اكتب /exit للمغادرة", "dim"))
            try:
                if input(paint("exit? [y/N] ", "gold", enabled=color)).strip().lower() == "y":
                    break
            except EOFError:
                break


# ----------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Aali CLI — terminal client")
    ap.add_argument("-q", "--quick", help="one-shot question, print answer, exit")
    ap.add_argument("--markdown", help="send a file's text content to Aali")
    ap.add_argument("--base", default=os.environ.get("AALI_BASE", "http://127.0.0.1:5055"))
    ap.add_argument("--key", default=os.environ.get("AALI_API_KEY", ""), help="X-API-Key")
    ap.add_argument("--theme", default=os.environ.get("AALI_THEME", ""),
                    help="colour theme: gold | matrix | ocean "
                         "(default: saved theme from ~/.aali_cli_theme)")
    args = ap.parse_args()

    if args.theme:
        if not set_theme(args.theme):
            print(paint(f"✗ unknown theme {args.theme!r} — choose one of: {', '.join(THEMES)}", "red"))
            sys.exit(2)
    else:
        load_saved_theme()

    if args.markdown:
        text = Path(args.markdown).read_text(encoding="utf-8", errors="replace")
        one_shot(args.base, text[:20000], api_key=args.key)
        return
    if args.quick:
        one_shot(args.base, args.quick, api_key=args.key)
        return
    repl(args.base, api_key=args.key)


if __name__ == "__main__":
    main()
