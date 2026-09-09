# -*- coding: utf-8 -*-
"""Shared GPU/VRAM gate for every trainer launcher on this machine.

One source of truth for "is the 8GB card safe for a new job" - used by
soup_pipeline.py, today_chain.py, night_caretaker.py and extend_context.bat
(import it, or run it as a CLI from a .bat). Replaces the per-script
nvidia-smi polling copies that had drifted apart (some checked VRAM only,
some checked compute processes only) after the 2026-09-09 OOM chain.

Safe means BOTH:
  1. no python-family compute process holds VRAM (soup server, exam server,
     soup train, another train_scratch), and
  2. used VRAM is below the threshold (desktop idle on the 8GB card is
     ~400-1500 MiB; a served 1.5B adapter is ~4-6GB).

Fail-closed: if nvidia-smi is missing/unreadable the check reports NOT safe.
Launchers must treat "not safe" as "do not launch" - the CLI exits 2 and
never prints GO in that case (--allow-degraded overrides, visibly).

Log: appends one line per decision to D:/hwk-data/gpu_gate.log.

Library API:
  card_is_safe(threshold_mib=1500) -> (bool, reason)
  wait_until_safe(threshold_mib=1500, timeout_s=7200, poll_s=10,
                  on_wait=None) -> (bool, reason)

CLI:
  python scripts/wait_gpu_free.py                      # poll up to 2 h
  python scripts/wait_gpu_free.py --max-wait 900       # 15 min then fail
  python scripts/wait_gpu_free.py --threshold 1500     # idle cutoff (MiB)
  python scripts/wait_gpu_free.py --allow-degraded     # never block
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LOG = Path("D:/hwk-data/gpu_gate.log")
TRAINING_LOG = Path("D:/hwk-data/training.log")
DEFAULT_TRAINING_IDLE_MINUTES = 15
QUERY_VRAM = ("nvidia-smi", "--query-gpu=memory.used",
              "--format=csv,noheader,nounits")
QUERY_APPS = ("nvidia-smi", "--query-compute-apps=pid,process_name",
              "--format=csv,noheader")
PYTHON_MARKERS = ("python", "soup", "ptxas")  # trainers + soup server/train


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _run(cmd: tuple[str, ...]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def vram_used_mib() -> int | None:
    """Used VRAM in MiB, or None when nvidia-smi is unusable (fail-closed)."""
    out = _run(QUERY_VRAM)
    if not out:
        return None
    try:
        return int(out.splitlines()[-1].strip())
    except (IndexError, ValueError):
        return None


def python_compute_pids() -> list[int] | None:
    """PIDs of python/soup/ptxas processes holding a CUDA context (trainers,
    servers) - None when the query itself fails (callers must treat that as
    'cannot verify' and refuse, never as 'no processes'). Browsers/DWM also
    hold contexts on Windows - not trainers, so only python-family names
    block."""
    out = _run(QUERY_APPS)
    if out is None:
        return None
    pids: list[int] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        name = parts[1].lower()
        if any(marker in name for marker in PYTHON_MARKERS):
            try:
                pids.append(int(parts[0]))
            except ValueError:
                continue
    return pids


def training_log_idle_minutes() -> float | None:
    """Minutes since training.log was last written (None: no log to protect)."""
    try:
        return (time.time() - TRAINING_LOG.stat().st_mtime) / 60
    except OSError:
        return None


def card_is_safe(
    threshold_mib: int = 1500,
    require_training_log_idle: bool = False,
    training_idle_minutes: int = DEFAULT_TRAINING_IDLE_MINUTES,
) -> tuple[bool, str]:
    """True when the card can take a new trainer.

    require_training_log_idle adds soup_pipeline's/night_caretaker's extra
    guard: training.log must be stale (its writer heartbeats every ~1.5 min),
    which catches trainers whose CUDA context has not registered yet.
    """
    used = vram_used_mib()
    if used is None:
        return False, "nvidia-smi unavailable - cannot verify VRAM"
    pids = python_compute_pids()
    if pids is None:
        return False, ("nvidia-smi compute-apps unavailable - "
                       "cannot verify processes")
    if pids:
        return False, f"python/soup compute on GPU: pids {pids}"
    if used >= threshold_mib:
        return False, f"VRAM busy: {used} MiB >= {threshold_mib} MiB threshold"
    if require_training_log_idle:
        idle = training_log_idle_minutes()
        if idle is not None and idle < training_idle_minutes:
            return False, (f"training.log written {idle:.1f} min ago "
                           f"(<{training_idle_minutes})")
    return True, (f"VRAM idle: {used} MiB < {threshold_mib} MiB, "
                  "no python compute"
                  + (", training.log idle" if require_training_log_idle else ""))


def wait_until_safe(
    threshold_mib: int = 1500,
    timeout_s: int = 7200,
    poll_s: int = 10,
    on_wait=None,
    require_training_log_idle: bool = False,
    training_idle_minutes: int = DEFAULT_TRAINING_IDLE_MINUTES,
) -> tuple[bool, str]:
    """Poll until card_is_safe() or timeout. on_wait(reason) fires per retry
    (callers use it to log). Returns (safe, last_reason)."""
    deadline = time.monotonic() + timeout_s
    reason = "max-wait not reached yet"
    while time.monotonic() < deadline:
        ok, reason = card_is_safe(threshold_mib, require_training_log_idle,
                                  training_idle_minutes)
        if ok:
            return True, reason
        if on_wait is not None:
            on_wait(reason)
        time.sleep(poll_s)
    return False, reason


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait until the GPU is free")
    parser.add_argument("--threshold", type=int, default=1500,
                        help="max used VRAM (MiB) considered idle (default 1500)")
    parser.add_argument("--max-wait", type=int, default=7200,
                        help="give up after N seconds (exit 2, fail-closed)")
    parser.add_argument("--poll", type=int, default=10,
                        help="poll interval seconds (default 10)")
    parser.add_argument("--allow-degraded", action="store_true",
                        help="launch even when the check never passes (logs degraded=true)")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ok, reason = wait_until_safe(threshold_mib=args.threshold,
                                 timeout_s=args.max_wait, poll_s=args.poll,
                                 on_wait=lambda r: log(f"WAIT: {r}"))
    if ok:
        log(f"GO: {reason}")
        return 0
    if args.allow_degraded:
        log(f"DEGRADED LAUNCH (degraded=true): {reason}")
        return 0
    log(f"BLOCK (exit 2): {reason}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
