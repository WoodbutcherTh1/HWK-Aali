"""Send one question to arena.ai, then dump candidate message containers so we
can pick the right selector for arena_teach.py."""

from __future__ import annotations

import json
import threading
import time

import webview

LOG = "D:/hwk-data/arena_dom.json"
state: dict = {}

JS_FILL = (
    "(() => {"
    "const ta = [...document.querySelectorAll('textarea')].find(el => el.offsetWidth > 50);"
    "if (!ta) return 'no_input';"
    "const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;"
    f"setter.call(ta, {json.dumps('اكتب دالة بايثون تعكس سلسلة نصية', ensure_ascii=False)});"
    "ta.dispatchEvent(new Event('input', {bubbles:true})); return 'filled';"
    "})()"
)
JS_SEND = (
    "(() => { const b = [...document.querySelectorAll('button')].find(x => "
    "((x.getAttribute('aria-label')||'').toLowerCase().match(/send|submit/) && !x.disabled) "
    "|| (x.type === 'submit' && !x.disabled)); if (b) { b.click(); return 'clicked'; } "
    "const ta = [...document.querySelectorAll('textarea')].find(el => el.offsetWidth > 50); "
    "if (ta) { ta.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true})); "
    "return 'enter'; } return 'none'; })()"
)
JS_DUMP = (
    "(() => {"
    "const kw = ['message','turn','chat','thread','conversation','bubble','assistant','user','prose','markdown','turns'];"
    "const seen = {};"
    "const all = document.querySelectorAll('div,section,article,main');"
    "for (const el of all) {"
    "  const cls = (el.className && el.className.baseVal !== undefined) ? el.className.baseVal : (el.className || '');"
    "  if (typeof cls !== 'string') continue;"
    "  const lc = cls.toLowerCase();"
    "  if (!kw.some(k => lc.includes(k))) continue;"
    "  const t = (el.innerText || '').trim();"
    "  if (t.length < 10) continue;"
    "  const key = cls.slice(0, 90);"
    "  if (!seen[key] || t.length > seen[key].len) seen[key] = {cls: key, len: t.length, head: t.slice(0, 120)};"
    "}"
    "return JSON.stringify({dump: Object.values(seen).sort((a,b) => b.len - a.len).slice(0, 18), "
    "bodyTail: (document.body.innerText || '').slice(-600)});"
    "})()"
)


def run(window) -> None:
    time.sleep(14)
    try:
        state["load"] = window.evaluate_js("document.title + ' | ' + location.href")
    except Exception as exc:  # noqa: BLE001
        state["load_err"] = str(exc)
    try:
        state["fill"] = window.evaluate_js(JS_FILL)
        time.sleep(1)
        state["send"] = window.evaluate_js(JS_SEND)
    except Exception as exc:  # noqa: BLE001
        state["send_err"] = str(exc)
    time.sleep(40)  # let the model answer
    try:
        state["dump"] = json.loads(window.evaluate_js(JS_DUMP) or "{}")
    except Exception as exc:  # noqa: BLE001
        state["dump_err"] = str(exc)
    try:
        window.destroy()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    win = webview.create_window("arena dom probe", "https://arena.ai", width=1280, height=920)
    webview.start(run, win, private_mode=False, storage_path="D:/hwk-data/webview_profile")
    with open(LOG, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)
    print("dom probe done")
