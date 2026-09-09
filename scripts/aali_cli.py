# -*- coding: utf-8 -*-
"""Aali CLI — a pro-style terminal client for the Aali server.

Colorful, animated, Linux-terminal look: banner box, gold prompt, live spinner
while Aali thinks, tool lines streaming as he works (live tool traces
traces), markdown-ish rendering, follow-up suggestion chips, slash commands.

Usage:
    .venv\\Scripts\\python.exe scripts\\aali_cli.py              # interactive
    .venv\\Scripts\\python.exe scripts\\aali_cli.py -q "2+2?"    # one-shot
    .venv\\Scripts\\python.exe scripts\\aali_cli.py --markdown   # pipe file
    .venv\\Scripts\\python.exe scripts\\aali_cli.py --theme matrix
    .venv\\Scripts\\python.exe scripts\\aali_cli.py --base http://127.0.0.1:5055

Keys: ↑/↓ history · ←/→ edit · TAB complete · Ctrl+C cancel
      /exit quits, /new starts a fresh session, /open N resumes,
      /clear clears the screen, /theme switches the colour theme,
      /tools lists Aali's abilities, /multi pastes multi-line text,
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

# ------------------------------------------------- Arabic display (bidi/shaping)
# Classic Windows consoles (conhost) have NO bidi reordering and NO Arabic
# shaping: logical-order Arabic prints disconnected and mirrored (owner
# screenshot 2026-09-09: "آلي — مساعدك المحلي" came out as
# "يلآ .زهاج ،يلحلا ديسم"). We shape into Unicode presentation forms and
# reorder runs for display ourselves — stdlib-only so the exe stays free.
# Windows Terminal (WT_SESSION) and friends do bidi natively: there the
# transform must stay OFF or letters get double-reversed. /bidi toggles.

# base letter -> (isolated, final, initial, medial) presentation forms
_ARABIC_FORMS: dict[str, tuple[int | None, int | None, int | None, int | None]] = {
    "ء": (0xFE80, None, None, None),
    "آ": (0xFE81, 0xFE82, None, None),
    "أ": (0xFE83, 0xFE84, None, None),
    "ؤ": (0xFE85, 0xFE86, None, None),
    "إ": (0xFE87, 0xFE88, None, None),
    "ئ": (0xFE89, 0xFE8A, 0xFE8B, 0xFE8C),
    "ا": (0xFE8D, 0xFE8E, None, None),
    "ب": (0xFE8F, 0xFE90, 0xFE91, 0xFE92),
    "ة": (0xFE93, 0xFE94, None, None),
    "ت": (0xFE95, 0xFE96, 0xFE97, 0xFE98),
    "ث": (0xFE99, 0xFE9A, 0xFE9B, 0xFE9C),
    "ج": (0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0),
    "ح": (0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4),
    "خ": (0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8),
    "د": (0xFEA9, 0xFEAA, None, None),
    "ذ": (0xFEAB, 0xFEAC, None, None),
    "ر": (0xFEAD, 0xFEAE, None, None),
    "ز": (0xFEAF, 0xFEB0, None, None),
    "س": (0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4),
    "ش": (0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8),
    "ص": (0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC),
    "ض": (0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0),
    "ط": (0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4),
    "ظ": (0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8),
    "ع": (0xFEC9, 0xFECA, 0xFECB, 0xFECC),
    "غ": (0xFECD, 0xFECE, 0xFECF, 0xFED0),
    "ف": (0xFED1, 0xFED2, 0xFED3, 0xFED4),
    "ق": (0xFED5, 0xFED6, 0xFED7, 0xFED8),
    "ك": (0xFED9, 0xFEDA, 0xFEDB, 0xFEDC),
    "ل": (0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0),
    "م": (0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4),
    "ن": (0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8),
    "ه": (0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC),
    "و": (0xFEED, 0xFEEE, None, None),
    "ى": (0xFEEF, 0xFEF0, None, None),
    "ي": (0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4),
}


# Extended Arabic-script letters (Persian/Urdu/Kurdish: پ چ ژ ک گ ی …) are
# derived from Unicode itself at import time: every codepoint in the Arabic
# Presentation Forms blocks carries a decomposition tag (<isolated>/<final>/
# <initial>/<medial>) plus its base letter. Typo-proof and complete — owner
# request 2026-09-09.
import unicodedata as _ud

for _cp in range(0xFB50, 0xFE00):
    _decomp = _ud.decomposition(chr(_cp))
    if not _decomp.startswith("<"):
        continue
    _tag, _, _rest = _decomp.partition(" ")
    _parts = _rest.split()
    if len(_parts) != 1:  # lam-alef ligatures decompose to 2 letters — skip
        continue
    _base = chr(int(_parts[0], 16))
    if _base not in _ARABIC_FORMS:  # core Arabic table above stays canonical
        _ARABIC_FORMS[_base] = (None, None, None, None)
    _idx = {"<isolated>": 0, "<final>": 1, "<initial>": 2, "<medial>": 3}[_tag]
    _forms = list(_ARABIC_FORMS[_base])
    if _forms[_idx] is None:
        _forms[_idx] = _cp
    _ARABIC_FORMS[_base] = tuple(_forms)
#
# (right-joining classification for the derived letters is appended right
# after _RIGHT_JOINING is defined below — reh-family never joins forward.)
# lam + alef-variant ligatures: pair -> (isolated, final)
_LAM_ALEF = {"لا": (0xFEFB, 0xFEFC), "لأ": (0xFEF7, 0xFEF8),
             "لإ": (0xFEF9, 0xFEFA), "لآ": (0xFEF5, 0xFEF6)}
# letters that connect only to the PREVIOUS letter (no initial/medial form)
_RIGHT_JOINING = set("ءآأؤإاةدذرزوى")
# derived extended letters that join only backward too (reh-family: ژ ڈ ڑ ں ے)
_RIGHT_JOINING.update({"ژ", "ڈ", "ڑ", "ں", "ے"})
# combining marks (harakat): pass through, never affect joining
_MARKS = set(chr(c) for c in range(0x064B, 0x0660)) | {chr(0x0670)}
_HAS_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
# presentation forms never occur in logical source text — seeing one means
# the string was already display-processed (fx must be idempotent)
_PRESENTATION_RE = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")
# runs that must stay LTR inside RTL text: latin, digits, urls, numbers
_LTR_RUN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+\-/%:#@]*")
_MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")


def shape_arabic(text: str) -> str:
    """Logical Arabic -> contextual presentation forms (lam-alef ligatures,
    initial/medial/final/isolated selection). Non-Arabic passes through."""
    out: list[str] = []
    prev_connects = False  # previous Arabic letter connects forward to this one
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _MARKS:
            out.append(ch)
            i += 1
            continue
        pair = text[i:i + 2]
        if ch == "ل" and pair in _LAM_ALEF:
            iso, fin = _LAM_ALEF[pair]
            out.append(chr(fin if prev_connects else iso))
            prev_connects = False
            i += 2
            continue
        if ch in _ARABIC_FORMS:
            iso, fin, ini, med = _ARABIC_FORMS[ch]
            joins_fwd = (ch not in _RIGHT_JOINING and i + 1 < n
                         and (text[i + 1] in _ARABIC_FORMS or text[i + 1] in _MARKS
                              and i + 2 < n and text[i + 2] in _ARABIC_FORMS))
            if prev_connects and joins_fwd and med is not None:
                out.append(chr(med))
            elif prev_connects and fin is not None:
                out.append(chr(fin))
            elif joins_fwd and ini is not None:
                out.append(chr(ini))
            elif iso is not None:
                out.append(chr(iso))
            else:
                out.append(ch)
            prev_connects = joins_fwd
            i += 1
            continue
        out.append(ch)
        prev_connects = False
        i += 1
    return "".join(out)


def _reverse_rtl(chunk: str) -> str:
    """Reverse one RTL run for visual order; keep harakat attached to their
    base letter and mirror paired brackets."""
    units: list[str] = []
    i = 0
    while i < len(chunk):
        unit = chunk[i]
        i += 1
        while i < len(chunk) and chunk[i] in _MARKS:
            unit += chunk[i]
            i += 1
        units.append(unit)
    units.reverse()
    return "".join(units).translate(_MIRROR)


def reorder_visual(text: str) -> str:
    """Shaped logical order -> visual order for an LTR terminal: RTL runs
    are reversed, LTR runs (latin/digits/urls) kept intact, run order
    reversed so the sentence reads right-to-left on screen."""
    tokens: list[tuple[bool, str]] = []
    i, n = 0, len(text)
    while i < n:
        m = _LTR_RUN.match(text, i)
        if m:
            tokens.append((True, m.group(0)))
            i = m.end()
            continue
        j = i + 1
        while j < n and not _LTR_RUN.match(text, j):
            j += 1
        tokens.append((False, text[i:j]))
        i = j
    parts: list[str] = []
    for is_ltr, chunk in reversed(tokens):
        parts.append(chunk if is_ltr else _reverse_rtl(chunk))
    return "".join(parts)


def _wrap_logical(text: str, width: int) -> list[str]:
    """Word-aware wrap of LOGICAL-order text before shaping: the terminal
    breaks a long line into screen rows, and since the reorder is per-row,
    each wrapped row must be a coherent logical slice — else the second row
    reads backwards."""
    lines: list[str] = []
    for raw in text.splitlines() or [""]:
        words = raw.split(" ")
        cur = ""
        for word in words:
            candidate = f"{cur} {word}" if cur else word
            if len(candidate) > width and cur:
                lines.append(cur)
                cur = word
            else:
                cur = candidate
            while len(cur) > width:  # single word longer than the row
                lines.append(cur[:width])
                cur = cur[width:]
        lines.append(cur)
    return lines


def fx(text: str, width: int = 0) -> str:
    """Display fix: shape + reorder Arabic. With `width` set, overlong lines
    are wrapped at that many cells FIRST (logical order), so terminal-wrapped
    Arabic stays readable (owner report 2026-09-09). Idempotent (an
    already-shaped string is returned unchanged) and a no-op on pure-Latin."""
    if _PRESENTATION_RE.search(text) or not _HAS_ARABIC_RE.search(text):
        return text
    if width and len(text) > width:
        return "\n".join(
            reorder_visual(shape_arabic(line)) for line in _wrap_logical(text, width))
    return reorder_visual(shape_arabic(text))


BIDI_FILE = Path.home() / ".aali_cli_bidi"
BIDI_MODE = False  # display transform active? (set by apply_bidi_pref)


def _terminal_bidi_capable() -> bool:
    """Terminals that do bidi/shaping themselves — where our transform would
    double-reverse the text."""
    if os.environ.get("WT_SESSION"):  # Windows Terminal
        return True
    if os.environ.get("TERM_PROGRAM") in {
            "vscode", "iTerm.app", "WezTerm", "Apple_Terminal", "mintty"}:
        return True
    if os.environ.get("ANSICON") or os.environ.get("ConEmuANSI") == "ON":
        return True
    return False


def load_bidi_pref() -> str:
    try:
        saved = BIDI_FILE.read_text(encoding="utf-8").strip().lower()
        if saved in {"on", "off", "auto"}:
            return saved
    except OSError:
        pass
    return "auto"


def save_bidi_pref(value: str) -> None:
    try:
        BIDI_FILE.write_text(value + "\n", encoding="utf-8")
    except OSError:
        pass


def apply_bidi_pref() -> None:
    global BIDI_MODE
    pref = load_bidi_pref()
    BIDI_MODE = (pref == "on" or (pref == "auto" and not _terminal_bidi_capable()))


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


def _term_width() -> int:
    try:
        return max(20, shutil.get_terminal_size().columns - 1)
    except Exception:  # noqa: BLE001
        return 100


def paint(text: str, *styles: str, enabled: bool = True) -> str:
    if enabled and BIDI_MODE:
        text = fx(text, _term_width())  # shape + reorder (single choke point)
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
            # fx is idempotent, so paint may shape again (a no-op) when colour
            # is on — applying it here keeps headings/bullets fixed even with
            # colour disabled (BIDI_MODE is a terminal property, not a colour one).
            body = fx(m.group(1)) if BIDI_MODE else m.group(1)
            if kind == "h":
                print(paint(body, "bold", "gold", enabled=color))
            elif kind == "li":
                print(paint("  • ", "green", enabled=color) + body)
            elif kind == "oli":
                tail = fx(m.group(2)) if BIDI_MODE else m.group(2)
                print(paint(f"  {m.group(1)}. ", "green", enabled=color) + tail)
            break
        else:
            out = line
            out = re.sub(r"\*\*(.+?)\*\*", lambda mm: mm.group(1), out)
            out = re.sub(r"`([^`]+)`", lambda mm: f"{mm.group(1)}", out)
            print(fx(out, _term_width()) if BIDI_MODE else out)


# ----------------------------------------------------------------- streaming
def stream_ask(base: str, message: str, sid: str, confirm: bool = False,
               api_key: str = "") -> dict:
    """POST /api/ask/stream and consume the SSE events with a live spinner.

    Activity events render as live tool traces; the `done`
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


# ----------------------------------------------------------------- line editor
HISTORY_FILE = Path.home() / ".aali_cli_history"
HISTORY_MAX = 500

_KEYS = {"UP", "DOWN", "LEFT", "RIGHT", "TAB", "ENTER", "BS", "DEL", "HOME", "END"}
_SLASH_COMMANDS = ("/exit", "/quit", "/q", "/help", "/new", "/open", "/clear",
                   "/theme", "/tools", "/multi", "/sid", "/bidi")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _make_completer(base: str):
    """TAB completion: slash commands, and theme/tool names after those."""
    tools_cache: list[str] | None = None

    def completer(text: str) -> list[str]:
        nonlocal tools_cache
        if text.startswith("/theme "):
            arg = text[len("/theme "):]
            return [t for t in THEMES if t.startswith(arg)]
        if text.startswith("/tools "):
            arg = text[len("/tools "):]
            if tools_cache is None:
                try:
                    data = http_json(base, "/api/tools", timeout=3)
                    tools_cache = [t["name"] for t in data.get("tools", [])]
                except Exception:  # noqa: BLE001
                    tools_cache = []
            return [t for t in tools_cache if t.startswith(arg)]
        if text.startswith("/"):
            return [c for c in _SLASH_COMMANDS if c.startswith(text)]
        return []

    return completer


class LineEditor:
    """Raw-mode line editor: ↑/↓ history, ←/→ editing, TAB completion, and
    paste-safe multi-line input (a fast burst of 40+ chars containing
    newlines — i.e. a pasted code block — opens continuation lines instead
    of submitting).

    Standard library only (msvcrt / termios+tty) so the PyInstaller exe
    stays dependency-free. Falls back to plain input() when stdin is not a
    TTY, and to injected keys (`read(keys=…)`) for tests.
    """

    BURST_MIN = 40  # kept for reference: bursts are detected by newline, not size

    def __init__(self, history_path: Path, completer=None):
        self.path = history_path
        self.completer = completer
        self.history: list[str] = []
        self._load()

    # ---- history storage -------------------------------------------------
    def _load(self) -> None:
        try:
            self.history = [
                h for h in (l.rstrip("\n") for l in self.path
                            .read_text(encoding="utf-8", errors="replace")
                            .splitlines())
                if h.strip()
            ][-HISTORY_MAX:]
        except OSError:
            self.history = []

    def remember(self, line: str) -> None:
        if not line.strip() or (self.history and self.history[-1] == line):
            return
        self.history = (self.history + [line])[-HISTORY_MAX:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(self.history) + "\n", encoding="utf-8")
        except OSError:
            pass

    # ---- key readers ------------------------------------------------------
    @staticmethod
    def _raw_available() -> bool:
        try:
            return sys.stdin.isatty()
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _reader_windows():
        import msvcrt

        def read_key() -> str:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):  # special-key prefix
                code = msvcrt.getwch()
                return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                        "S": "DEL", "G": "HOME", "O": "END"}.get(code, "")
            if ch == "\t":
                return "TAB"
            if ch in ("\r", "\n"):
                return "ENTER"
            if ch in ("\x7f", "\x08"):
                return "BS"
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch

        def poll() -> str:
            if msvcrt.kbhit():
                return read_key()
            time.sleep(0.012)
            return msvcrt.getwch() if msvcrt.kbhit() else ""

        return read_key, poll

    @staticmethod
    def _reader_posix():
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)  # no echo/canonical; ISIG stays on (Ctrl+C works)

        def read_key() -> str:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                if not select.select([sys.stdin], [], [], 0.05)[0]:
                    return ""  # bare Escape — ignore
                seq = sys.stdin.read(2)
                return {"[A": "UP", "[B": "DOWN", "[D": "LEFT", "[C": "RIGHT",
                        "[3~": "DEL", "[H": "HOME", "[F": "END"}.get(seq, "")
            if ch == "\t":
                return "TAB"
            if ch in ("\r", "\n"):
                return "ENTER"
            if ch in ("\x7f", "\x08"):
                return "BS"
            return ch

        def poll() -> str:
            if select.select([sys.stdin], [], [], 0.012)[0]:
                return read_key()
            return ""

        return read_key, poll, lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old)

    # ---- rendering ---------------------------------------------------------
    @staticmethod
    def _rows(n: int, width: int) -> int:
        """Terminal rows needed for `n` visible columns (≥1)."""
        return max(1, -(-n // width))

    def _redraw(self, out, prompt: str, prompt_len: int,
                lines: list[str], li: int, pos: int, color: bool) -> None:
        width = shutil.get_terminal_size((100, 24)).columns
        rows = [self._rows(prompt_len + len(lines[0]), width)]
        rows += [self._rows(len(l), width) for l in lines[1:]]
        out.write("\r" + "\x1b[1A" * (sum(rows) - 1) + "\x1b[J")
        out.write(prompt + lines[0]
                  + (("\n" + "\n".join(lines[1:])) if len(lines) > 1 else ""))
        col = pos + (prompt_len if li == 0 else 0)
        below = sum(rows[i] for i in range(li + 1, len(lines))) \
            + rows[li] - 1 - col // width
        if below > 0:
            out.write("\x1b[1A" * below)
        out.write("\r\x1b[{}C".format(col % width))
        out.flush()

    # ---- main read loop -----------------------------------------------------
    def read(self, prompt: str, color: bool = True,
             keys: list[str] | None = None) -> str | None:
        """Read one logical input (single- or multi-line). None = cancelled."""
        if keys is None and not self._raw_available():
            return input(prompt) or None
        out = sys.stdout
        is_win = os.name == "nt"
        restore = None
        if keys is not None:
            seq = list(keys)

            def read_key() -> str:
                if not seq:
                    raise KeyboardInterrupt  # exhausted injection = cancel
                return seq.pop(0)

            poll = lambda: ""  # noqa: E731
        elif is_win:
            read_key, poll = self._reader_windows()
        else:
            read_key, poll, restore = self._reader_posix()
        prompt_len = len(_ANSI_RE.sub("", prompt))
        lines, li, pos = [""], 0, 0
        ml = False  # multi-line mode
        self._hidx, self._draft = len(self.history), ""
        out.write(prompt)
        out.flush()
        try:
            while True:
                k = read_key()
                if k == "":
                    continue
                if k in _KEYS:
                    if k == "ENTER":
                        if not ml and lines[li].endswith("\\"):
                            # bash-style continuation: trailing \ opens a line
                            lines[li] = lines[li][:-1]
                            lines.insert(li + 1, "")
                            li, pos = li + 1, 0
                            ml = True
                            self._redraw(out, prompt, prompt_len, lines, li, pos, color)
                            continue
                        if ml:
                            if lines[li].strip() == "":
                                lines.pop(li)
                                li = max(0, li - 1)
                                pos = len(lines[li])
                                break  # submit on empty line
                            lines.insert(li + 1, "")
                            li, pos = li + 1, 0
                        else:
                            break  # submit
                    elif k == "BS":
                        if pos > 0:
                            lines[li] = lines[li][:pos - 1] + lines[li][pos:]
                            pos -= 1
                        elif li > 0:  # merge into previous line
                            prev = lines[li - 1]
                            lines[li - 1] = prev + lines[li]
                            del lines[li]
                            li, pos = li - 1, len(prev)
                    elif k == "DEL":
                        if pos < len(lines[li]):
                            lines[li] = lines[li][:pos] + lines[li][pos + 1:]
                        elif li < len(lines) - 1:
                            lines[li] += lines.pop(li + 1)
                    elif k == "LEFT":
                        if pos > 0:
                            pos -= 1
                        elif li > 0:
                            li, pos = li - 1, len(lines[li - 1])
                    elif k == "RIGHT":
                        if pos < len(lines[li]):
                            pos += 1
                        elif li < len(lines) - 1:
                            li, pos = li + 1, 0
                    elif k in ("UP", "DOWN"):
                        if ml:
                            li = max(0, li - 1) if k == "UP" \
                                else min(len(lines) - 1, li + 1)
                            pos = min(pos, len(lines[li]))
                        elif k == "UP" and self.history:
                            if self._hidx == len(self.history):
                                self._draft = lines[0]
                            if self._hidx > 0:
                                self._hidx -= 1
                                lines[0] = self.history[self._hidx]
                                pos = len(lines[0])
                        elif k == "DOWN" and self._hidx < len(self.history):
                            self._hidx += 1
                            lines[0] = self.history[self._hidx] \
                                if self._hidx < len(self.history) else self._draft
                            pos = len(lines[0])
                    elif k == "HOME":
                        pos = 0
                    elif k == "END":
                        pos = len(lines[li])
                    elif k == "TAB" and self.completer and li == 0:
                        cands = sorted(set(self.completer(lines[0][:pos])))
                        if cands:
                            common = os.path.commonprefix(cands)
                            if len(common) > len(lines[0][:pos]):
                                lines[0] = common + lines[0][pos:]
                                pos = len(common)
                            elif len(cands) > 1:
                                out.write("\n"
                                          + paint("  " + "  ".join(cands), "grey",
                                                  enabled=color) + "\n")
                                out.write(prompt + lines[0] + "\r\x1b[{}C".format(
                                    pos + prompt_len))
                                out.flush()
                    self._redraw(out, prompt, prompt_len, lines, li, pos, color)
                    continue
                # text burst: 1+ printable chars; a paste may contain newlines
                chunk = k + (poll() or "")
                if "\n" in chunk or "\r" in chunk:
                    chunk = chunk.replace("\r\n", "\n").replace("\r", "\n")
                    parts = chunk.split("\n")
                    if len(parts) == 2 and parts[1] == "":
                        # single-line paste with trailing newline → submit it
                        lines[li] = lines[li][:pos] + parts[0] + lines[li][pos:]
                        break
                    ml = True  # real multi-line paste → continuation lines
                    for i, part in enumerate(parts):
                        if i:
                            lines.insert(li + 1, "")
                            li += 1
                            pos = 0
                        lines[li] = lines[li][:pos] + part + lines[li][pos:]
                        pos += len(part)
                else:
                    lines[li] = lines[li][:pos] + chunk + lines[li][pos:]
                    pos += len(chunk)
                self._redraw(out, prompt, prompt_len, lines, li, pos, color)
        except (KeyboardInterrupt, EOFError):
            out.write("\r" + paint("✻ cancelled", "cyan", enabled=color))
            return None
        finally:
            if restore:
                restore()
        text = "\n".join(lines).strip()
        out.write("\r\x1b[J")  # clear the rendered input; the REPL reprints it
        out.flush()
        if text:
            self.remember(text)
        return text or None


# ----------------------------------------------------------------- REPL
SLASH = "/\\/_-"  # harmless constant for lint friendliness

# Static fallback when /api/tools is unreachable — keep in sync with the
# server's tool registry (agent_loop._ollama_core_tools).
_TOOLS_FALLBACK: list[tuple[str, list[tuple[str, str]]]] = [
    ("ملفات — files", [
        ("write_file", "create or overwrite a file"),
        ("append_file", "append text to a file"),
        ("read_file", "read a text file"),
        ("list_files", "list workspace files and folders"),
        ("search_files", "search text across files"),
        ("make_directory", "create a folder"),
        ("move_file", "move or rename files"),
        ("delete_file", "delete a file"),
    ]),
    ("أوامر — commands", [
        ("run_command", "run allowed commands (python/pytest/node/npm/git)"),
    ]),
    ("وسائط — media", [
        ("read_image", "OCR text inside images"),
        ("read_document", "read PDF / Word / Excel documents"),
        ("analyze_video", "analyze video (frame OCR + audio transcript)"),
    ]),
    ("أتمتة — automation", [
        ("make_n8n_workflow", "generate an importable n8n workflow"),
    ]),
]


def repl(base: str, api_key: str = "") -> None:
    ensure_server(base)
    color = _supports_color()
    width = shutil.get_terminal_size((100, 24)).columns
    banner(width)
    sid = "cli-" + str(int(time.time()))
    last_suggestions: list[str] = []
    cols = {"user": "gold", "aali": "green", "info": "cyan", "warn": "red"}
    editor = LineEditor(HISTORY_FILE, _make_completer(base))

    def send(text: str) -> None:
        """One full chat turn: stream, confirm dangerous actions, render."""
        nonlocal last_suggestions
        result = stream_ask(base, text, sid, api_key=api_key)
        if not result:
            return
        reply = str(result.get("reply", ""))
        if result.get("needs_confirm"):
            act = result.get("pending_action") or {}
            print(paint("⚠ ", "red", enabled=color) + paint(
                f"آلي يطلب إذناً بتنفيذ: {act.get('tool')} {act.get('arguments', {})}",
                "bold", enabled=color))
            ans = input(paint("   allow? [y/N] ", "gold", enabled=color)).strip().lower()
            if ans == "y":
                result = stream_ask(base, text, sid, confirm=True, api_key=api_key)
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

    def say(tag: str, text: str, style: str) -> None:
        body = fx(text, _term_width()) if BIDI_MODE else text
        print(paint(f"{tag} ", style, "bold", enabled=color) + body)

    while True:
        try:
            prompt = paint("❯ ", "gold", enabled=color)
            message = editor.read(prompt, color)
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
                    print(paint("  /tools what Aali can do · /multi paste a multi-line block", "dim"))
                    print(paint("  /sid show session id · /bidi Arabic display fix (on/off/auto) · /exit quit", "dim"))
                    print(paint("  TAB completes commands · ↑/↓ history · trailing \\ continues the line", "dim"))
                    continue
                if cmd == "/bidi":
                    arg = arg.strip().lower()
                    if arg in {"on", "off", "auto"}:
                        save_bidi_pref(arg)
                        apply_bidi_pref()
                        state = "ACTIVE" if BIDI_MODE else "inactive"
                        say("✻", "arabic display fix → " + arg + " (" + state + " here)", "info")
                    else:
                        state = "on" if BIDI_MODE else "off"
                        print(paint(f"  arabic display fix: {state} "
                                    f"(pref: {load_bidi_pref()})", "dim", enabled=color))
                        print(paint("  use when Arabic shows disconnected/backwards "
                                    "(classic conhost) — /bidi on | off | auto", "dim", enabled=color))
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
                            body = content[:400].replace("\n", " ")
                            if BIDI_MODE:
                                body = fx(body)
                            print(paint(f"{who} ", style, "bold", enabled=color) + body)
                    except Exception as exc:  # noqa: BLE001
                        say("✗", f"session fetch failed: {exc}", "warn")
                    continue
                if cmd == "/tools":
                    try:
                        data = http_json(base, "/api/tools", timeout=4)
                    except Exception:  # noqa: BLE001
                        data = None
                    print()
                    if data and data.get("tools"):
                        rows = [(str(t.get("name", "")), str(t.get("description", "")))
                                for t in data["tools"]]
                        print(paint(f"  آلي can use {len(rows)} tools (live from the server):",
                                    "bold", "gold", enabled=color))
                        for name, desc in rows:
                            print(paint("  ● ", "green", enabled=color)
                                  + paint(f"{name:<18}", "bold", enabled=color)
                                  + paint(desc, "grey", enabled=color))
                    else:
                        print(paint("  آلي's tools (server unreachable — built-in list):",
                                    "bold", "gold", enabled=color))
                        for gname, items in _TOOLS_FALLBACK:
                            print(paint(f"  {gname}", "bold", "cyan", enabled=color))
                            for name, desc in items:
                                print(paint("    ● ", "green", enabled=color)
                                      + paint(f"{name:<18}", "bold", enabled=color)
                                      + paint(desc, "grey", enabled=color))
                    print()
                    continue
                if cmd == "/multi":
                    say("✻", "multi-line — الصق أو اكتب، وسطر فارغ للإرسال (Ctrl+C إلغاء)", "info")
                    parts: list[str] = []
                    while True:
                        try:
                            line = input(paint("… ", "gold", enabled=color))
                        except (EOFError, KeyboardInterrupt):
                            parts = []
                            break
                        if not line.strip():
                            break
                        parts.append(line)
                    if parts:
                        send("\n".join(parts))
                    else:
                        say("✻", "cancelled", "info")
                    continue
                say("✗", f"أمر غير معروف: {cmd} — جرّب /help", "warn")
                continue

            # plain chat turn (single- or multi-line input)
            send(message)
        except EOFError:
            break
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
    ap.add_argument("--bidi", default=os.environ.get("AALI_BIDI", ""),
                    help="Arabic display fix: on | off | auto "
                         "(default: saved pref from ~/.aali_cli_bidi)")
    args = ap.parse_args()

    if args.bidi:
        if args.bidi.lower() not in {"on", "off", "auto"}:
            print(paint(f"✗ --bidi must be on | off | auto, got {args.bidi!r}", "red"))
            sys.exit(2)
        save_bidi_pref(args.bidi.lower())
    apply_bidi_pref()

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
