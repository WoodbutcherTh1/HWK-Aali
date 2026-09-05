"""Probe arena.ai's anonymous chat UI: does a textbox exist, what does the page offer?"""

from __future__ import annotations

import json
import threading
import time

import webview

LOG = "D:/hwk-data/arena_probe.json"
state: dict = {}


def _js(window) -> str:
    return r"""
    (function () {
      const out = {title: document.title, url: location.href, textarea: 0,
                   inputs: 0, buttons: [], nav: []};
      out.textarea = document.querySelectorAll('textarea').length;
      out.inputs = document.querySelectorAll('input[type=text], input:not([type])').length;
      const btns = [...document.querySelectorAll('button')].slice(0, 25);
      btns.forEach(b => { const t = (b.innerText || '').trim(); if (t && t.length < 40) out.buttons.push(t); });
      const body = document.body ? document.body.innerText.slice(0, 800) : '';
      out.sample = body;
      return JSON.stringify(out);
    })();
    """


def probe(window) -> None:
    time.sleep(16)  # let the SPA settle
    try:
        raw = window.evaluate_js(_js(window))
        state["js"] = json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        state["error"] = f"evaluate_js failed: {exc}"
    try:
        state["title"] = window.title
        window.destroy()
    except Exception as exc:  # noqa: BLE001
        state["error2"] = str(exc)


if __name__ == "__main__":
    win = webview.create_window("arena probe", "https://arena.ai", width=1280, height=920)
    webview.start(probe, win, private_mode=False, storage_path="D:/hwk-data/webview_profile")
    with open(LOG, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)
    print("probe done")
