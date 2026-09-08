"""Desktop mentor A: capture a strong desktop assistant's answers on this PC
as training examples for the local model.

The app is launched (or brought to the front), the prompt is pasted (so Arabic
and code work), and the reply is captured via the clipboard and saved to
D:\\hwk-data\\teacher\\<timestamp>.jsonl (also printed to stdout).

Requirements: mentor installed and signed in once by the user, plus
pywinauto/pyautogui/pyperclip in the venv. This automates a third-party GUI and
can break when the mentor app updates; keep answers for personal research only.

Usage:
  python mentor_a.py "Explain overfitting in one short Arabic sentence"
  python mentor_a.py --file questions.txt --timeout 300
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# AUMID of the installed mentor app of the vendor MSIX package.
MENTOR_APP_AUMID = "Claude_pzs8sxrjxfjjc!Claude"
DEFAULT_OUT_DIR = Path("D:/hwk-data/teacher")


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _is_running() -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title_length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, buffer, title_length + 1)
        if "claude" in buffer.value.lower() and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(callback), 0)
    return bool(found)


def _launch() -> None:
    os.startfile(f"shell:AppsFolder\\{MENTOR_APP_AUMID}")  # type: ignore[attr-defined]


def _mentor_pids() -> set[int]:
    import subprocess

    output = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV"],
        capture_output=True, text=True,
    ).stdout
    pids: set[int] = set()
    for line in output.splitlines()[1:]:
        parts = [part.strip('"') for part in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _find_candidate_windows() -> list[tuple[int, int, int, bool]]:
    """Return [(hwnd, area, iconic, zero_rect)] for the mentor app main windows."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pids = _mentor_pids()
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
        if "claude" not in title.value.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        iconic = bool(user32.IsIconic(hwnd))
        results.append((hwnd, area, iconic, area <= 0))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(callback), 0)
    return results


def _locate_main_window() -> tuple[int, tuple[int, int, int, int]]:
    """Find, restore, and focus the main mentor window; return (hwnd, box)."""
    candidates = _find_candidate_windows()
    if not candidates:
        raise SystemExit("mentor window not found. Is mentor running and signed in?")
    # Prefer the largest on-screen window; restore a minimized one if needed.
    candidates.sort(key=lambda item: item[1], reverse=True)
    hwnd, area, iconic, zero_rect = candidates[0]
    if iconic or zero_rect:
        import ctypes

        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(2.0)
    _foreground(hwnd)
    _ensure_on_screen(hwnd)
    time.sleep(1.5)
    return hwnd, _window_box(hwnd)


def _ensure_on_screen(hwnd: int) -> None:
    """If the window sits outside the virtual screen (e.g. a monitor was
    unplugged), move it back so mouse automation can reach it."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top = rect.left, rect.top
    width = max(rect.right - rect.left, 200)
    height = max(rect.bottom - rect.top, 200)
    virtual_left = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    virtual_top = user32.GetSystemMetrics(77)    # SM_YVIRTUALSCREEN
    virtual_width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    virtual_height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    on_screen = (
        left < virtual_left + virtual_width - 100
        and top < virtual_top + virtual_height - 100
        and left + 100 > virtual_left
        and top + 100 > virtual_top
    )
    if not on_screen:
        user32.MoveWindow(hwnd, virtual_left + 60, virtual_top + 60, min(width, virtual_width - 120), min(height, virtual_height - 140), True)
        time.sleep(1.0)


def _window_box(hwnd: int) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def _foreground(hwnd: int) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(1.0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    user32.SetForegroundWindow(hwnd)  # second call defeats focus stealing guard
    time.sleep(0.5)


def _ask_once(hwnd: int, box: tuple[int, int, int, int], prompt: str, timeout: int = 240) -> str:
    import pyautogui
    import pyperclip

    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    _foreground(hwnd)

    # The mentor app's composer sits at the bottom of the window.
    composer_x = left + int(width * 0.5)
    composer_y = bottom - int(height * 0.08)
    pyautogui.click(composer_x, composer_y)
    time.sleep(0.8)
    pyperclip.copy(prompt)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.0)
    pyautogui.press("enter")
    return _wait_and_copy(hwnd, box, prompt, timeout=timeout)


def _wait_and_copy(hwnd: int, box: tuple[int, int, int, int], prompt: str, timeout: int) -> str:
    """Wait for generation to settle, then grab the transcript via Ctrl+A/C."""
    import pyautogui
    import pyperclip

    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    # A fresh conversation sits near the bottom of the window, just above the
    # composer; clicking there lands inside the newest message so Ctrl+A/C
    # selects the transcript instead of empty space.
    transcript_x = left + int(width * 0.5)
    transcript_y = bottom - int(height * 0.18)

    started = time.time()
    stable_for = 0
    previous: str | None = None
    min_wait = 8.0
    while time.time() - started < timeout:
        time.sleep(3)
        elapsed = time.time() - started
        if elapsed < min_wait:
            continue
        _foreground(hwnd)
        pyautogui.click(transcript_x, transcript_y)
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.3)
        text = (pyperclip.paste() or "").strip()
        if text and text == previous:
            stable_for += 1
        else:
            stable_for = 0
        previous = text
        if stable_for >= 2 and text:
            return _extract_reply(text, prompt)
    raise RuntimeError(f"No stable reply captured within {timeout}s.")


_NOISE_LINES = {
    "write a message…", "write a message...", "اكتب رسالة…", "اكتب رسالة...",
    "please double-check responses.",
    "كلود هو ذكاء اصطناعي وقد يخطئ. يرجى التحقق من الإجابات.",
}


def _extract_reply(transcript: str, prompt: str) -> str:
    """Take everything after the last occurrence of the prompt text."""
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
        # Fallback: drop the first line if it echoes the prompt.
        lines = transcript.splitlines()
        if lines and (marker in lines[0] or lines[0].strip() == marker):
            reply = "\n".join(lines[1:])
        else:
            reply = transcript
    # Trim trailing UI chrome lines (composer placeholder, disclaimer footer).
    cleaned = []
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped.lower() in _NOISE_LINES:
            continue
        if not stripped and cleaned and not cleaned[-1]:
            continue
        cleaned.append(line)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned).strip()


def ask(prompt: str, out_dir: Path, timeout: int = 240) -> str:
    if not prompt.strip():
        raise SystemExit("prompt must not be empty")
    if not _is_running():
        print("mentor is not running; launching it...")
        _launch()
        time.sleep(8)
    hwnd, box = _locate_main_window()
    print(f"found mentor window hwnd={hwnd} box={box}", flush=True)
    try:
        reply = _ask_once(hwnd, box, prompt, timeout=timeout)
    except Exception:
        try:
            import pyautogui

            debug = out_dir / "debug_screenshot.png"
            out_dir.mkdir(parents=True, exist_ok=True)
            pyautogui.screenshot(str(debug))
            print(f"debug screenshot: {debug}", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        raise
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "id": uuid.uuid4().hex[:12],
        "source": "mentor-a",
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
    parser = argparse.ArgumentParser(description="Ask mentor and save the answer.")
    parser.add_argument("prompt", nargs="?", default=None, help="Question to ask the mentor.")
    parser.add_argument("--file", default=None, help="File with one question per line.")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="Output folder.")
    parser.add_argument("--timeout", type=int, default=240, help="Seconds to wait for a reply.")
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
        except Exception as exc:  # noqa: BLE001 - report and continue the queue
            print(f"[error] {exc}", file=sys.stderr)
            continue
        print("\n--- REPLY ---\n" + reply + "\n--- END ---\n", flush=True)


if __name__ == "__main__":
    main()
