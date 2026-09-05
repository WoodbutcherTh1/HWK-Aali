"""Cursor teacher: open the Cursor app on this PC, ask it in its chat panel to
build or explain something, and capture the answer (plus files it creates).

Cursor is an AI code editor; its chat panel (Ctrl+L) can create and edit files
in the open project folder. Answers are saved to D:\\hwk-data\\teacher with
source "cursor", and any files it writes stay in the project folder so they can
be harvested as code examples later.

Usage:
  python cursor_teach.py "Build a small guessing game in Python in this folder"
  python cursor_teach.py --file questions.txt
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUT_DIR = Path("D:/hwk-data/teacher")
CURSOR_EXE = Path("X:/cursor/Cursor.exe")
PROCESS_NAME = "Cursor.exe"
OCR_PS1 = Path(__file__).resolve().parent / "win_ocr.ps1"


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _launch() -> None:
    project = os.getenv("CURSOR_PROJECT", "D:/hwk-projects/cursor_demo")
    if CURSOR_EXE.exists():
        subprocess.Popen([str(CURSOR_EXE), project])
    else:
        os.startfile("cursor")  # type: ignore[attr-defined]


def _process_pids() -> set[int]:
    output = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {PROCESS_NAME}", "/FO", "CSV"],
        capture_output=True, text=True,
    ).stdout
    pids: set[int] = set()
    for line in output.splitlines()[1:]:
        parts = [part.strip('"') for part in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _candidate_windows() -> list[tuple[int, int, int, bool]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pids = _process_pids()
    results: list[tuple[int, int, int, bool]] = []

    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        title_length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title, title_length + 1)
        if "cursor" not in title.value.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        results.append((hwnd, area, int(user32.IsIconic(hwnd)), area <= 0))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(callback), 0)
    return results


def _foreground(hwnd: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
        time.sleep(1.0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)


def _ensure_on_screen(hwnd: int) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = max(rect.right - rect.left, 300)
    height = max(rect.bottom - rect.top, 300)
    v_left = user32.GetSystemMetrics(76)
    v_top = user32.GetSystemMetrics(77)
    v_width = user32.GetSystemMetrics(78)
    v_height = user32.GetSystemMetrics(79)
    on_screen = (
        rect.left < v_left + v_width - 100 and rect.top < v_top + v_height - 100
        and rect.left + 100 > v_left and rect.top + 100 > v_top
    )
    if not on_screen:
        user32.MoveWindow(hwnd, v_left + 60, v_top + 60, min(width, v_width - 120), min(height, v_height - 140), True)
        time.sleep(1.0)


def _main_window() -> tuple[int, tuple[int, int, int, int]]:
    candidates = _candidate_windows()
    if not candidates:
        raise SystemExit("Cursor window not found. Is Cursor running and signed in?")
    candidates.sort(key=lambda item: item[1], reverse=True)
    hwnd, area, iconic, zero_rect = candidates[0]
    if iconic or zero_rect:
        import ctypes

        ctypes.windll.user32.ShowWindow(hwnd, 9)
        time.sleep(2.0)
    _foreground(hwnd)
    _ensure_on_screen(hwnd)
    time.sleep(1.5)
    return hwnd, _window_box(hwnd)


def _window_box(hwnd: int) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def _ask_once(hwnd: int, box: tuple[int, int, int, int], prompt: str, timeout: int) -> str:
    import pyautogui
    import pyperclip

    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    _foreground(hwnd)
    pyautogui.hotkey("ctrl", "l")  # open/focus the chat composer
    time.sleep(1.5)
    pyperclip.copy(prompt)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.0)
    pyautogui.press("escape")  # dismiss any suggestion tooltip
    time.sleep(0.4)
    pyautogui.press("enter")
    return _wait_and_copy(hwnd, box, prompt, timeout=timeout)


def _ocr_window(box: tuple[int, int, int, int]) -> list[dict[str, object]]:
    """Screenshot the window region and OCR it; return text lines with boxes."""
    import json as _json
    import subprocess as _subprocess
    import tempfile

    from PIL import ImageGrab

    left, top, right, bottom = box
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    temporary = Path(tempfile.mkstemp(suffix=".png")[1])
    try:
        image = ImageGrab.grab(bbox=(left, top, right, bottom))
        image.save(str(temporary))
        output = _subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_PS1), str(temporary)],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        lines = _json.loads(output) if output else []
        return lines if isinstance(lines, list) else []
    except Exception as exc:  # noqa: BLE001
        print(f"  OCR failed: {exc}", flush=True)
        return []
    finally:
        for _ in range(5):
            try:
                temporary.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.5)


def _ocr_text(box: tuple[int, int, int, int]) -> str:
    return "\n".join(str(line.get("text", "")) for line in _ocr_window(box)).strip()


def _click_send_button(box: tuple[int, int, int, int]) -> bool:
    """If OCR finds a Send button, click it. Returns True when clicked."""
    import pyautogui

    for line in _ocr_window(box):
        text = str(line.get("text", "")).strip().lower()
        if text in ("send", "→", "send message", "submit"):
            x = (int(line["left"]) + int(line["right"])) // 2 + box[0]
            y = (int(line["top"]) + int(line["bottom"])) // 2 + box[1]
            pyautogui.click(x, y)
            return True
    return False


def _wait_and_copy(hwnd: int, box: tuple[int, int, int, int], prompt: str, timeout: int) -> str:
    """Poll OCR until the chat is stable, then return the text after the prompt."""
    started = time.time()
    stable_for = 0
    previous: str | None = None
    sent_click_attempted = False
    while time.time() - started < timeout:
        time.sleep(4)
        if time.time() - started < 10:
            continue
        _foreground(hwnd)
        text = _ocr_text(box)
        if not text:
            continue
        if text == previous:
            stable_for += 1
        else:
            stable_for = 0
        previous = text
        if stable_for >= 2 and text:
            reply = _extract_reply(text, prompt)
            if len(reply) >= 40:
                return reply
        # If nothing changed after ~20s, try clicking an explicit Send button.
        if time.time() - started > 25 and not sent_click_attempted:
            if _click_send_button(box):
                sent_click_attempted = True
                print("  clicked Send button", flush=True)
    raise RuntimeError(f"No stable reply captured within {timeout}s.")


_NOISE_LINES = {
    "write a message…", "write a message...", "اكتب رسالة…", "اكتب رسالة...",
}


def _extract_reply(transcript: str, prompt: str) -> str:
    marker = prompt.strip()
    candidates: list[int] = []
    start = 0
    while True:
        found = transcript.find(marker, start)
        if found < 0:
            break
        candidates.append(found)
        start = found + 1
    if candidates:
        reply = transcript[candidates[-1] + len(marker):]
    else:
        lines = transcript.splitlines()
        if lines and (marker in lines[0] or lines[0].strip() == marker):
            reply = "\n".join(lines[1:])
        else:
            reply = transcript
    cleaned = []
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped.lower() in _NOISE_LINES:
            continue
        cleaned.append(line)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned).strip()


def ask(prompt: str, out_dir: Path, timeout: int = 240) -> str:
    if not prompt.strip():
        raise SystemExit("prompt must not be empty")
    if not _process_pids():
        print("Cursor is not running; launching it...")
        _launch()
        time.sleep(12)
    hwnd, box = _main_window()
    print(f"found Cursor window hwnd={hwnd} box={box}", flush=True)
    reply = _ask_once(hwnd, box, prompt, timeout=timeout)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "id": uuid.uuid4().hex[:12],
        "source": "cursor",
        "prompt": prompt,
        "reply": reply,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"saved: {destination}", flush=True)
    return reply


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Ask Cursor's chat panel and save the answer.")
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("--file", default=None)
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    questions: list[str] = []
    if args.file:
        with Path(args.file).open(encoding="utf-8") as handle:
            questions = [line.strip() for line in handle if line.strip()]
    elif args.prompt:
        questions = [args.prompt]
    if not questions:
        raise SystemExit("Give a prompt argument or --file questions.txt")

    for question in questions:
        try:
            reply = ask(question, Path(args.out), timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {exc}", file=sys.stderr)
            continue
        print("\n--- REPLY ---\n" + reply + "\n--- END ---\n", flush=True)


if __name__ == "__main__":
    main()