#!/usr/bin/env python3
"""The browser half of آلي ريتش — renders ONE public page, read-only.

Runs inside its own venv (``D:/hwk-tools/reach-venv``) because Playwright drags
in a browser and AGENTS.md forbids heavy deps in the training venv. It speaks
JSON on stdout and nothing else; ``file_agent.reach`` calls it as a subprocess.

    python reach_fetch.py --url https://example.com --engine auto

Engines:
  * ``chromium`` — Playwright's bundled Chromium (the default; fast).
  * ``camoufox`` — the Firefox-based anti-detect browser, for hosts that serve a
    challenge page to an obvious bot.
  * ``auto``     — chromium, then camoufox if the page came back thin.

**Read-only is enforced here, in the browser, not by good intentions:**

1. Only GET and HEAD leave the page. Any other method is aborted at the route
   layer, so page JavaScript cannot turn a read into a write.
2. Navigation to ``javascript:`` / ``data:`` / ``file:`` / any non-http(s)
   scheme is aborted.
3. Every request target is re-checked against the same SSRF fence the static
   path uses, resolved per request. Without this, a hostile page could simply
   ``fetch('http://127.0.0.1:5055/api/ask')`` from inside the browser — which is
   the attack a "read-only" reader most obviously invites.
4. A fresh throwaway context per fetch: no persistent profile, no cookies kept,
   no storage, no credentials of any kind.

What remains, stated honestly: a hostile page still executes JavaScript in an
isolated browser that holds no secrets and no filesystem. Rendering a page
means running its code; the containment is isolation, not refusal.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

MAX_TEXT_CHARS = 20_000
MAX_REQUESTS = 220          # a real page is a few dozen; a scraper is hundreds
ALLOWED_METHODS = {"GET", "HEAD"}


def _fence(url: str) -> bool:
    """True when this request may go out. Same rules as the static path."""
    try:
        from file_agent import reach  # the single source of truth for the fence
    except Exception:  # noqa: BLE001 - running outside the repo layout
        try:
            sys.path.insert(0, str(HERE.parent / "file-agent"))
            from file_agent import reach  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            return False
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        reach.guard_host(host, port)
        return True
    except Exception:  # noqa: BLE001 - an unfenceable target is a blocked target
        return False


def _launch(playwright, engine: str):
    """Return (browser, name). Camoufox only when it is actually installed."""
    if engine == "camoufox":
        from camoufox.sync_api import Camoufox  # noqa: PLC0415
        return Camoufox(headless=True), "camoufox"
    if engine == "auto-camoufox":
        try:
            from camoufox.sync_api import Camoufox  # noqa: PLC0415
            return Camoufox(headless=True), "camoufox"
        except Exception:  # noqa: BLE001
            pass
    return playwright.chromium.launch(headless=True), "chromium"


def fetch(url: str, engine: str, timeout: int) -> dict:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    blocked: list[str] = []
    seen = {"requests": 0}

    with sync_playwright() as playwright:
        browser, name = _launch(playwright, engine)
        try:
            context = browser.new_context(
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                            "Version/17.0 Safari/605.1.15"),
                viewport={"width": 1280, "height": 900},
                java_script_enabled=True,
                # No ambient authority of any kind.
                storage_state=None,
                accept_downloads=False,
                bypass_csp=False,
            )
            context.set_default_timeout(timeout * 1000)
            page = context.new_page()

            def route_handler(route, request):
                seen["requests"] += 1
                if seen["requests"] > MAX_REQUESTS:
                    blocked.append(request.url[:80])
                    route.abort()
                    return
                if (request.method or "GET").upper() not in ALLOWED_METHODS:
                    blocked.append(f"{request.method} {request.url[:60]}")
                    route.abort()
                    return
                if not _fence(request.url):
                    blocked.append(f"fenced {request.url[:60]}")
                    route.abort()
                    return
                try:
                    route.continue_()
                except Exception:  # noqa: BLE001 - a navigation we lost is fine
                    pass

            page.route("**/*", route_handler)
            response = page.goto(url, wait_until="domcontentloaded",
                                 timeout=timeout * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001 - many pages never go idle
                pass
            title = page.title()
            text = page.evaluate(
                "() => (document.body && document.body.innerText) || ''")
            links = page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]'))"
                ".slice(0,60).map(a => a.href)")
            images = page.evaluate(
                "() => Array.from(document.querySelectorAll('img'))"
                ".slice(0,30).map(i => i.currentSrc || i.src)")
            final_url = page.url
            status = response.status if response else 0
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
            browser.close()

    caps = []
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        caps.append("text_capped")
    if blocked:
        # Reported, never hidden: the owner should know the page tried to do
        # something this reader refused to do.
        caps.append(f"blocked_requests:{len(blocked)}")
    return {
        "ok": True,
        "engine": name,
        "status": status,
        "url": final_url,
        "title": title,
        "text": text,
        "links": [u for u in links if u.startswith(("http://", "https://"))],
        "images": [u for u in images if u.startswith(("http://", "https://"))],
        "bytes": len(text.encode("utf-8")),
        "caps": caps,
        "blocked_sample": blocked[:5],
        "content_type": "text/html",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aali Reach browser bridge")
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--engine", default="auto",
                        choices=["auto", "chromium", "camoufox", "auto-camoufox"])
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401,PLC0415
    except ImportError:
        print(json.dumps({"ok": False, "error": "playwright is not installed in "
                              "this venv — run scripts\\setup_reach.bat"},
                         ensure_ascii=False))
        return 2

    engines = ([args.engine] if args.engine in ("chromium", "camoufox")
               else ["auto-camoufox", "chromium"])
    last = ""
    for engine in engines:
        try:
            print(json.dumps(fetch(args.url, engine, args.timeout),
                             ensure_ascii=False))
            return 0
        except Exception as exc:  # noqa: BLE001 - try the next engine honestly
            last = f"{type(exc).__name__}: {exc}"[:200]
    print(json.dumps({"ok": False, "error": last or "the browser read nothing"},
                     ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())