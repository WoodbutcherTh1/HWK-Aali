"""DeepSeek teacher: ask the official DeepSeek web chat (chat.deepseek.com)
from an embedded WebView2 window and save the answers as training examples.

DeepSeek has no official Windows desktop app, so this uses the WebView2 engine
already built into Windows to open the official chat site. Sign in once in the
window (email/phone), and your session is remembered for later runs.

Usage:
  python deepseek_teacher.py "ما الفرق بين الذاكرة RAM و ROM؟ اشرح بثلاث جمل"
  python deepseek_teacher.py --file questions.txt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import webview

DEFAULT_OUT_DIR = Path("D:/hwk-data/teacher")
CHAT_URL = "https://chat.deepseek.com/"


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _load_state() -> str:
    return (
        "JSON.stringify({state: [...document.querySelectorAll('textarea')].length ? 'ready' : 'no_input', "
        "url: location.href, body: (document.body.innerText || '').slice(0, 200)})"
    )


def _fill_script(prompt: str) -> str:
    payload = json.dumps(prompt, ensure_ascii=False)
    return (
        "(() => {"
        "const ta = [...document.querySelectorAll('textarea')].find(el => el.offsetWidth > 50);"
        "if (!ta) return JSON.stringify({state:'no_input'});"
        "const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;"
        f"setter.call(ta, {payload});"
        "ta.dispatchEvent(new Event('input', {bubbles:true}));"
        "return JSON.stringify({state:'filled', len: ta.value.length});"
        "})()"
    )


def _send_script() -> str:
    return (
        "(() => {"
        "const buttons = [...document.querySelectorAll('button')];"
        "const send = buttons.find(b => /send|إرسال|发送/i.test(b.getAttribute('aria-label') || '') && !b.disabled)"
        "|| buttons.find(b => b.type === 'submit' && !b.disabled);"
        "if (send) { send.click(); return JSON.stringify({state:'clicked'}); }"
        "const ta = [...document.querySelectorAll('textarea')].find(el => el.offsetWidth > 50);"
        "if (ta) { ta.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true})); "
        "return JSON.stringify({state:'enter'}); }"
        "return JSON.stringify({state:'no_button'});"
        "})()"
    )


def _last_markdown_script() -> str:
    return (
        "(() => { const els = [...document.querySelectorAll('[class*=ds-markdown]')]; "
        "return JSON.stringify({count: els.length, last: els.length ? els[els.length - 1].innerText.trim() : ''}); })()"
    )


def _teacher_loop(window, questions: list[str], out_dir: Path, timeout: int) -> list[dict[str, object]]:
    # Wait for the page and an input box; if the user is not signed in they get
    # a clear hint and time to do it in the window.
    print("opening DeepSeek chat...", flush=True)
    deadline = time.time() + 120
    state: dict[str, object] = {}
    while time.time() < deadline:
        try:
            state = json.loads(window.evaluate_js(_load_state()) or "{}")
        except Exception as exc:  # noqa: BLE001
            state = {}
            print(f"  page not ready: {exc}", flush=True)
        if state.get("state") == "ready":
            break
        if state.get("state") == "no_input" and time.time() < deadline - 90:
            print(
                "No chat input found yet. If you are not signed in, sign in now "
                "inside the DeepSeek window (email/phone). Waiting...",
                flush=True,
            )
        time.sleep(4)
    if state.get("state") != "ready":
        print(
            "Could not reach a signed-in DeepSeek chat (input box not found). "
            f"Page state: {state}",
            file=sys.stderr,
        )
        raise SystemExit("DeepSeek sign-in required — open chat.deepseek.com in the window and log in.")

    records: list[dict[str, object]] = []
    for prompt in questions:
        baseline: str = ""
        try:
            baseline_result = json.loads(window.evaluate_js(_last_markdown_script()) or "{}")
            if isinstance(baseline_result.get("last"), str):
                baseline = baseline_result["last"]
        except Exception:  # noqa: BLE001
            baseline = ""
        filled = json.loads(window.evaluate_js(_fill_script(prompt)) or "{}")
        if filled.get("state") != "filled":
            print(f"  could not fill the composer: {filled}", flush=True)
            continue
        time.sleep(1.0)
        sent = json.loads(window.evaluate_js(_send_script()) or "{}")
        print(f"prompt sent (state={sent.get('state')})", flush=True)
        if sent.get("state") not in ("clicked", "enter"):
            print(f"  could not send prompt: {sent}", flush=True)
            continue
        reply, last, stable = "", "", 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2.5)
            try:
                snapshot = json.loads(window.evaluate_js(_last_markdown_script()) or "{}")
            except Exception:  # noqa: BLE001
                continue
            text = str(snapshot.get("last", "")).strip()
            if text == baseline:
                text = ""
            if text and text == last:
                stable += 1
            elif text:
                stable = 0
            last = text or last
            if stable >= 3 and last:
                reply = last
                break
        reply = reply or last or ""
        if len(reply.strip()) < 40:
            print("  no usable reply captured", flush=True)
            continue
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "id": uuid.uuid4().hex[:12],
            "source": "deepseek-web",
            "prompt": prompt,
            "reply": reply,
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
    parser = argparse.ArgumentParser(description="Ask DeepSeek web chat and save the answer.")
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("--file", default=None, help="File with one question per line.")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--timeout", type=int, default=180, help="Seconds to wait for each reply.")
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
    results: list[dict[str, object]] = []

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
        "DeepSeek Teacher (HWK)", CHAT_URL,
        width=1050, height=750, min_size=(900, 600),
    )
    # private_mode=False keeps the login/session for the next run.
    webview.start(runner, private_mode=False)

    if not results:
        raise SystemExit("No DeepSeek answers were captured.")
    for record in results:
        print("\n--- REPLY ---\n" + str(record["reply"]) + "\n--- END ---\n", flush=True)


if __name__ == "__main__":
    main()
