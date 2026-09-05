"""Arena teacher: ask frontier models through arena.ai (the community leaderboard
arena) from an embedded WebView2 window and save answers as training examples.

arena.ai is free, anonymous chat with frontier models (Claude, GPT, Gemini and
more). NOTE: the site shares conversations with the AI providers and may publish
them for research — only ever send non-sensitive educational questions here.

Usage:
  python arena_teach.py "اكتب دالة بايثون تعكس نصاً؟"
  python arena_teach.py --file questions.txt
Pick the model you want in the visible window before the run if a picker shows.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import webview

DEFAULT_OUT_DIR = Path("D:/hwk-data/teacher")
CHAT_URL = "https://arena.ai/"

# Candidates for the chat message transcript, tried in order (first with
# elements wins). Last element of the best candidate = the newest reply.
SELECTOR_CANDIDATES = [
    "[class*=markdown]",
    "[class*=prose]",
    "[class*=message]:not([class*=input])",
    "[class*=answer]",
]

_JS_LOAD = (
    "JSON.stringify({state: [...document.querySelectorAll('textarea')].some("
    "el => el.offsetWidth > 50) ? 'ready' : 'no_input', "
    "url: location.href, body: (document.body.innerText || '').slice(0, 150)})"
)

_JS_FILL = lambda prompt: (  # noqa: E731 - deliberate simple builder
    "(() => {"
    "const ta = [...document.querySelectorAll('textarea')].find(el => el.offsetWidth > 50);"
    "if (!ta) return JSON.stringify({state:'no_input'});"
    "const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;"
    f"setter.call(ta, {json.dumps(prompt, ensure_ascii=False)});"
    "ta.dispatchEvent(new Event('input', {bubbles:true}));"
    "return JSON.stringify({state:'filled', len: ta.value.length});"
    "})()"
)

_JS_SEND = (
    "(() => {"
    "const buttons = [...document.querySelectorAll('button')];"
    "const send = buttons.find(b => { const l = (b.getAttribute('aria-label') || '').toLowerCase(); "
    "return /send|submit/.test(l) && !b.disabled; })"
    "|| buttons.find(b => (b.type === 'submit') && !b.disabled);"
    "if (send) { send.click(); return JSON.stringify({state:'clicked'}); }"
    "const ta = [...document.querySelectorAll('textarea')].find(el => el.offsetWidth > 50);"
    "if (ta) { ta.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true})); "
    "return JSON.stringify({state:'enter'}); }"
    "return JSON.stringify({state:'no_button'});"
    "})()"
)

_JS_LAST_TEXT = (
    "(() => {"
    "for (const sel of " + json.dumps(SELECTOR_CANDIDATES) + ") {"
    "  const els = [...document.querySelectorAll(sel)].filter(el => {"
    "    const t = (el.innerText || '').trim();"
    "    return t.length > 5 && t.length < 20000 && el.offsetWidth > 100;"
    "  });"
    "  if (els.length) {"
    "    const last = els[els.length - 1];"
    "    return JSON.stringify({count: els.length, last: last.innerText.trim(), via: sel});"
    "  }"
    "}"
    "const body = document.body ? (document.body.innerText || '') : '';"
    "return JSON.stringify({count: 0, last: body.slice(-3000), via: 'body'});"
    "})()"
)


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _snapshot(window) -> tuple[dict, str]:
    try:
        raw = window.evaluate_js(_JS_LAST_TEXT)
        data = json.loads(raw or "{}")
        return data, str(data.get("last", ""))
    except Exception as exc:  # noqa: BLE001
        return {}, f"evaluate error: {exc}"


def _teacher_loop(window, questions: list[str], out_dir: Path, timeout: int) -> list[dict]:
    print("opening arena.ai chat...", flush=True)
    deadline = time.time() + 120
    state: dict = {}
    while time.time() < deadline:
        try:
            state = json.loads(window.evaluate_js(_JS_LOAD) or "{}")
        except Exception as exc:  # noqa: BLE001
            state = {}
        if state.get("state") == "ready":
            break
        if time.time() > deadline - 60 and state.get("state") != "ready":
            print("  no chat input yet; if a login/model wall shows, pick a model in the window.", flush=True)
        time.sleep(4)
    if state.get("state") != "ready":
        raise SystemExit(f"arena.ai chat input not found. Page: {state.get('body', '')[:120]}")

    records: list[dict] = []
    for prompt in questions:
        _, baseline = _snapshot(window)
        filled = json.loads(window.evaluate_js(_JS_FILL(prompt)) or "{}")
        if filled.get("state") != "filled":
            print(f"  could not fill composer: {filled}", flush=True)
            continue
        time.sleep(1.0)
        sent = json.loads(window.evaluate_js(_JS_SEND) or "{}")
        print(f"prompt sent (state={sent.get('state')})", flush=True)
        if sent.get("state") not in ("clicked", "enter"):
            print(f"  could not send prompt: {sent}", flush=True)
            continue

        reply, last, stable = "", "", 0
        deadline = time.time() + timeout
        via = "?"
        while time.time() < deadline:
            time.sleep(2.5)
            data, text = _snapshot(window)
            via = data.get("via", via)
            text = text.strip()
            if text == baseline:
                text = ""
            if text and text == last:
                stable += 1
            elif text:
                stable = 0
            last = text or last
            if stable >= 4 and last:
                reply = last
                break
        reply = reply or last or ""
        # Trim to the newest answer: drop anything before our own prompt echo.
        marker = prompt[:60].strip()
        if marker and len(reply) > len(marker):
            idx = reply.rfind(marker)
            if idx >= 0:
                reply = reply[idx + len(marker):].strip()
        if len(reply.strip()) < 40:
            print(f"  no usable reply captured (via={via}, chars={len(reply)})", flush=True)
            continue
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "id": uuid.uuid4().hex[:12],
            "source": "arena-web",
            "prompt": prompt,
            "reply": reply.strip(),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        destination = out_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
        with destination.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        records.append(record)
        print(f"saved: {destination} (chars={len(reply)})", flush=True)
    return records


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Ask arena.ai and save the answer as a training example.")
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("--file", default=None, help="File with one question per line.")
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

    out_dir = Path(args.out)
    results: list[dict] = []

    def runner() -> None:
        nonlocal results
        window = webview.windows[0]
        try:
            results = _teacher_loop(window, questions, out_dir, args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {exc}", file=sys.stderr)
        finally:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

    webview.create_window(
        "Arena Teacher (HWK)", CHAT_URL,
        width=1150, height=800, min_size=(900, 600),
    )
    webview.start(runner, private_mode=False, storage_path="D:/hwk-data/webview_profile")

    if not results:
        raise SystemExit("No arena.ai answers were captured.")
    for record in results:
        print("\n--- REPLY ---\n" + str(record["reply"]) + "\n--- END ---\n", flush=True)


if __name__ == "__main__":
    main()
