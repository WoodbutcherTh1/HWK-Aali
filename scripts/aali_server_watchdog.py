#!/usr/bin/env python3
"""Aali server watchdog - revives the Flask server mid-session.

2026-09-22 lesson: the logon autostart guard only fires at boot, and the
server died silently between sessions anyway. This poller runs detached all
day, checks /api/health every CHECK_SECONDS, and when the server is down it
fires scripts/aali_autostart.bat - the SAME idempotent guard that boots at
logon - so every piece of boot logic lives in exactly one place.

Design rules inherited from this repo's scars:
- never console-bound (2026-09-12): start via scripts/launch_detached.py;
- single instance via pidlock (2026-09-22 01:44 double-tokenizer lesson);
- never kills anything: revival = spawn the guard, settle, re-check;
- log only state changes, to D:/hwk-data/aali_server_watchdog.log.

Usage:
  .venv/Scripts/python.exe scripts/aali_server_watchdog.py [--once]
  detached (the supported way):
    scripts/launch_detached.py --log aali_server_watchdog --
      .venv/Scripts/python.exe scripts/aali_server_watchdog.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = Path("D:/hwk-data")
PIDLOCK = DATA / "aali_server_watchdog.pidlock"
GUARD_BAT = REPO / "scripts" / "aali_autostart.bat"
DEFAULT_URL = "http://127.0.0.1:5055/api/health"

CHECK_SECONDS = 60
SETTLE_SECONDS = 45      # after firing the guard, let a cold boot finish
HTTP_TIMEOUT = 4

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(message: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def check_health(url: str = DEFAULT_URL, timeout: int = HTTP_TIMEOUT) -> bool:
    """True only when /api/health answers AND says ok:true."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        return bool(payload.get("ok"))
    except (OSError, ValueError):
        return False


def pid_running(pid: int) -> bool:
    """Is that pid a live process? (tasklist on Windows, kill-0 elsewhere).

    Cannot-verify counts as NOT running: a duplicate watchdog is harmless
    (the guard is idempotent), a missing watchdog is not."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW)
        return proc.returncode == 0 and str(pid) in proc.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def acquire_single_instance() -> bool:
    """False when another LIVE watchdog already owns the pidlock."""
    try:
        old = int(PIDLOCK.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        old = None
    if old is not None and old != os.getpid() and pid_running(old):
        return False
    PIDLOCK.parent.mkdir(parents=True, exist_ok=True)
    PIDLOCK.write_text(str(os.getpid()), encoding="ascii")
    return True


def fire_guard(guard_bat: Path = GUARD_BAT) -> bool:
    """Run the autostart guard synchronously; the guard decides what to do
    (boot when down, no-op when alive) and logs to its own file."""
    try:
        proc = subprocess.run(
            ["cmd", "/c", str(guard_bat)],
            capture_output=True, text=True, timeout=90,
            creationflags=CREATE_NO_WINDOW)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_cycle(*, url: str, guard_bat: Path, settle_seconds: int,
              down_fails: list) -> bool:
    """One poll. Returns True when the server is healthy at the end.
    ``down_fails`` is a caller-owned one-element consecutive-down counter."""
    if check_health(url):
        if down_fails[0]:
            log(f"server healthy again after {down_fails[0]} down check(s)")
        down_fails[0] = 0
        return True
    down_fails[0] += 1
    log(f"server DOWN (x{down_fails[0]}) -> firing autostart guard")
    ok = fire_guard(guard_bat)
    log(f"guard {'ran' if ok else 'FAILED to run'}; settling {settle_seconds}s")
    time.sleep(settle_seconds)
    revived = check_health(url)
    log("revived" if revived
        else "still down after guard - retrying next cycle")
    if revived:
        down_fails[0] = 0   # revived in THIS cycle: count from zero again
    return revived


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="one check cycle, then exit (tests)")
    parser.add_argument("--check-seconds", type=int, default=CHECK_SECONDS)
    parser.add_argument("--settle-seconds", type=int, default=SETTLE_SECONDS)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    if not acquire_single_instance():
        try:
            owner = PIDLOCK.read_text(encoding="ascii").strip()
        except OSError:
            owner = "?"
        log(f"already running (pid {owner}) - exiting")
        return 0

    log(f"watchdog started (pid {os.getpid()}, every {args.check_seconds}s, "
        f"url {args.url})")
    down_fails = [0]
    while True:
        try:
            run_cycle(url=args.url, guard_bat=GUARD_BAT,
                      settle_seconds=args.settle_seconds,
                      down_fails=down_fails)
        except Exception as exc:  # noqa: BLE001 - a watchdog must never die
            log(f"cycle error (continuing): {exc}")
        if args.once:
            break
        time.sleep(args.check_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
