#!/usr/bin/env python3
"""Post-revival live test of spawn_agents (2026-09-23).

Runs detached: waits for the :20129 brain to come back up (it revives when
stage 5 frees the card), then fires ONE real /api/ask that requires parallel
sub-agents, and logs the verdict to D:/hwk-data/spawn_live_test.log.

One-shot; exits after logging. Never spawns agents itself - the BRAIN does.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DATA = Path("D:/hwk-data")
LOG = DATA / "spawn_live_test.log"
BRAIN = "http://127.0.0.1:20129/v1/models"
ASK = "http://127.0.0.1:5055/api/ask"
SID = "spawn-live-e2e"

PROMPT = ("You have three INDEPENDENT small tasks: 1) list the Python files "
          "in the workspace root, 2) write a one-line haiku about servers, "
          "3) compute 17*23. These do not depend on each other - launch "
          "agents in parallel for them, then combine all three results in "
          "one final answer.")


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def brain_up() -> bool:
    try:
        with urllib.request.urlopen(BRAIN, timeout=5) as r:
            return bool(json.loads(r.read().decode()).get("data"))
    except (OSError, ValueError):
        return False


def ask(prompt: str, timeout_s: int = 420) -> dict:
    body = json.dumps({"message": prompt, "sid": SID}).encode("utf-8")
    req = urllib.request.Request(
        ASK, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    log("waiting for the brain (:20129) to come up (revives when stage 5 "
        "frees the card)...")
    deadline = time.time() + 8 * 3600          # give up after 8h, honestly
    while time.time() < deadline:
        if brain_up():
            log("brain is up - firing the live parallel-agents ask")
            break
        time.sleep(180)
    else:
        log("GAVE UP: brain never came up within 8h - no test fired")
        return 1

    time.sleep(30)                             # let the server fully settle
    try:
        reply = ask(PROMPT)
    except OSError as exc:
        log(f"ask failed: {exc}")
        return 1

    text = str(reply.get("reply", ""))
    ok = bool(reply.get("ok"))
    markers = {
        "haiku/servers text": any(w in text.lower() for w in
                                  ("server", "خادم", "سيرفر")),
        "17*23 result 391": "391" in text,
        "files listed": any(t in text for t in (".py", "ملف", "scripts")),
    }
    log(f"ok={ok}  reply_len={len(text)}  markers={markers}")
    log("VERDICT: " + ("PASS" if (ok and all(markers.values()))
                      else "PARTIAL - inspect the reply below"))
    log("--- reply start ---")
    for chunk in text[:2000].splitlines() or ["(empty)"]:
        log(chunk)
    log("--- reply end ---")
    return 0 if (ok and all(markers.values())) else 2


if __name__ == "__main__":
    raise SystemExit(main())
