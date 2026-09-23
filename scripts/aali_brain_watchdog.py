#!/usr/bin/env python3
"""Aali brain watchdog - revives the promoted brain (:20129) mid-session.

2026-09-23 lesson: the API (:5055) had a watchdog, the brain (:20129) did
not - the soup server died after the trainer took the card and nothing
revived it, so /api/ask returned the memory-backend connection error while
/api/health still said ok:true. This poller watches /v1/models on the brain
port and brings the promoted adapter back the same way the pipeline serves
it (soup serve), under one hard rule:

    revive ONLY when the GPU card is safe (wait_gpu_free.card_is_safe).
    While a trainer holds the card the revival WAITS - it never fights a
    training run for VRAM (the 2026-09-09 OOM chain rule).

House rules inherited from this repo's scars:
- never kills anything: if :20129 answers it is left completely alone,
  even if the served model id looks wrong (that is a human decision);
- revival = spawn `soup serve` detached (breakaway creation flags so a
  watchdog restart never sweeps the brain), then VERIFY the endpoint is up
  and serves the promoted adapter's id (the wrong-model lesson);
- single instance via pidlock (same contract as aali_server_watchdog);
- logs only state changes to D:/hwk-data/aali_brain_watchdog.log.

Operational note: when the brain is serving (~4-6GB VRAM) a NEW training
launch will find the card NOT safe and wait - stop this watchdog or the
brain by PID first if the training must start now (AALI_BRAIN_WATCHDOG_OFF=1
is the global kill switch).

Usage:
  .venv/Scripts/python.exe scripts/aali_brain_watchdog.py [--once]
  detached (the supported way):
    scripts/launch_detached.py --log aali_brain_watchdog --
      .venv/Scripts/python.exe scripts/aali_brain_watchdog.py
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
sys.path.insert(0, str(REPO / "scripts"))
import soup_pipeline as sp  # noqa: E402  (PORT, SOUP_EXE - one source of truth)
import wait_gpu_free as gate  # noqa: E402

DATA = Path("D:/hwk-data")
PIDLOCK = DATA / "aali_brain_watchdog.pidlock"
LOGFILE = DATA / "aali_brain_watchdog.log"
SERVE_LOG = DATA / "brain_serve.log"
PROMOTED_JSON = DATA / "soup" / "promoted.json"

CHECK_SECONDS = 120
READY_TIMEOUT_S = 900        # cold serve of the 1.5B adapter can take minutes
HTTP_TIMEOUT = 4
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    try:
        with LOGFILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def promoted_adapter() -> Path | None:
    """The adapter promoted.json says is the live brain (must exist)."""
    try:
        data = json.loads(PROMOTED_JSON.read_text(encoding="utf-8"))
        adapter = Path(str(data.get("adapter_dir", "")))
        return adapter if adapter.is_dir() else None
    except (OSError, ValueError):
        return None


def brain_up(port: int = sp.PORT, timeout: int = HTTP_TIMEOUT) -> tuple[bool, str | None]:
    """(healthy, served model id) for the brain endpoint."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/models", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        rows = data.get("data") or []
        return bool(rows), (str(rows[0].get("id")) if rows else None)
    except (OSError, ValueError):
        return False, None


def spawn_serve(adapter: Path) -> subprocess.Popen:
    """Start `soup serve` for the adapter, detached + breakaway, logging to
    brain_serve.log. The child intentionally outlives this watchdog."""
    flags = CREATE_NO_WINDOW
    try:
        import launch_detached
        flags |= launch_detached.detached_creationflags()
    except ImportError:
        pass
    SERVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    fh = SERVE_LOG.open("ab")
    return subprocess.Popen(
        [str(sp.SOUP_EXE), "serve", "--model", str(adapter),
         "--port", str(sp.PORT)],
        stdout=fh, stderr=subprocess.STDOUT,
        creationflags=flags)


def wait_ready(proc: subprocess.Popen, timeout_s: int = READY_TIMEOUT_S,
               port: int = sp.PORT) -> tuple[bool, str | None]:
    """Wait for /v1/models to answer; return (up, model_id)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False, None            # serve process died - report honestly
        up, model_id = brain_up(port)
        if up:
            return True, model_id
        time.sleep(10)
    return False, None


def run_cycle(*, probe=brain_up, safe=gate.card_is_safe, spawn=spawn_serve,
              ready=wait_ready, promoted=promoted_adapter, down_fails: list,
              quiet: dict) -> bool:
    """One poll. `down_fails` is a one-element consecutive-down counter;
    `quiet` remembers which waiting-reasons were already logged so a long
    trainer run does not spam the log. Returns True when the brain is up.

    Injectable probe/safe/spawn/ready/promoted: tests pin the contract
    without network, GPU, or real process spawns."""
    up, model_id = probe()
    if up:
        if down_fails[0]:
            log(f"brain healthy again after {down_fails[0]} down check(s) "
                f"(serving {model_id!r})")
        down_fails[0] = 0
        quiet.clear()
        return True

    down_fails[0] += 1
    adapter = promoted()
    if adapter is None:
        if not quiet.get("no_adapter"):
            log(f"brain DOWN (x{down_fails[0]}) and no existing promoted "
                f"adapter - nothing to revive (promoted.json)")
            quiet["no_adapter"] = True
        return False

    card_ok, reason = safe()
    if not card_ok:
        if quiet.get("wait") != reason:
            log(f"brain DOWN (x{down_fails[0]}) - card busy ({reason}); "
                f"revival waits (never fights the trainer)")
            quiet["wait"] = reason
        return False

    if down_fails[0] > 1 or quiet.get("wait"):
        log(f"brain DOWN (x{down_fails[0]}) and card free - reviving "
            f"{adapter.name}")
    else:
        log(f"brain DOWN - reviving {adapter.name}")
    quiet.pop("wait", None)
    proc = spawn(adapter)
    up, model_id = ready(proc)
    if not up:
        # Never kills anything: a late-serving process may still come up;
        # the next cycle's probe decides the truth.
        log(f"revival attempt: endpoint not ready in {READY_TIMEOUT_S}s - "
            f"leaving the serve process alone, next cycle decides")
        return False
    expected = adapter.name
    if model_id and expected not in model_id:
        log(f"WARNING: endpoint up but serves {model_id!r}, expected "
            f"{expected!r} - leaving it up (human decision), next cycle "
            f"re-checks")
        return True
    down_fails[0] = 0
    log(f"revived: {expected} serving on :{sp.PORT}")
    return True


def pid_running(pid: int) -> bool:
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
    try:
        old = int(PIDLOCK.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        old = None
    if old is not None and old != os.getpid() and pid_running(old):
        return False
    PIDLOCK.parent.mkdir(parents=True, exist_ok=True)
    PIDLOCK.write_text(str(os.getpid()), encoding="ascii")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true",
                        help="single poll cycle, then exit")
    args = parser.parse_args()

    if os.getenv("AALI_BRAIN_WATCHDOG_OFF") == "1":
        log("kill switch AALI_BRAIN_WATCHDOG_OFF=1 - exiting")
        return 0
    if not acquire_single_instance():
        return 0                     # duplicate is harmless, exit silently

    down_fails = [0]
    quiet: dict = {}
    log("brain watchdog started (pid %d, every %ds, port %d)"
        % (os.getpid(), CHECK_SECONDS, sp.PORT))
    while True:
        run_cycle(down_fails=down_fails, quiet=quiet)
        if args.once:
            return 0
        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
