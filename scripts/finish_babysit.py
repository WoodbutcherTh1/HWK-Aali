"""Babysitter: neutralize smoke-probe cycles while the OLD pipeline runs.

2026-09-12 14:47 decision: the live run's trainer starves host RAM (its own
memory keeps growing), so every remaining smoke-probe cycle costs the
trainer ~10 min of thrash and risks another empty-generation report (2/2
would abort a healthy run). The probes are TELEMETRY ONLY - worth nothing
next to the training itself. The old pipeline cannot be patched in-memory,
so this babysitter kills each cycle as it appears:

  1. the PROBE python first (an ungraded probe is 'unavailable' - it never
     writes an empty-report and never counts toward the 2/2 abort), then
  2. its CPU server on :20130 (exact PID from netstat - house rule: never
     kill by image name).

Exits when the old pipeline is gone (lock PID dead) or a fresh verdict
exists - the fixed pipeline + finish_pipeline.py own everything after that.
Never touches the trainer (port 20129), the finisher, or any other process.

Usage:
  python scripts/finish_babysit.py   (launched detached; logs to stdout)
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import soup_pipeline as sp  # noqa: E402
from finish_pipeline import LOCK_PATH, REPORT, verdict_is_fresh  # noqa: E402

PROBE_MARKER = "soup_probe_smoke.py"
SMOKE_PORT = sp.SMOKE_PORT
# A mid-load server is NOT listening yet and its cmdline lacks the probe
# marker - catch it by the serve+port pair (14:55 lesson: an orphaned server
# survived two polls and held 2.4 GB while the trainer starved).
SERVER_CMDLINE_MARKER = f"serve --model"
SERVER_PORT_MARKER = f"--port {SMOKE_PORT} "


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def old_pipeline_alive() -> bool:
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return False
    return pid > 0 and sp.psutil is not None and sp.psutil.pid_exists(pid)


def listening_pid(port: int) -> str | None:
    """Exact PID LISTENING on the port, or None (house rule: look up first)."""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            pid = line.split()[-1]
            if pid.isdigit():
                return pid
    return None


def probe_pids() -> list[str]:
    """PIDs of probe pythons (cmdline carries the marker) AND of the cycle's
    CPU-server pythons (cmdline has serve + the exact smoke port - covers a
    server that is still loading and not listening yet)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
             "| Select-Object ProcessId, CommandLine | ConvertTo-Json"],
            capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    import json

    try:
        payload = json.loads(out) if out.strip() else []
    except ValueError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    pids = []
    for entry in payload or []:
        command = str(entry.get("CommandLine", ""))
        is_probe = PROBE_MARKER in command
        is_smoke_server = (SERVER_CMDLINE_MARKER in command
                           and SERVER_PORT_MARKER in command)
        if is_probe or is_smoke_server:
            pids.append(str(entry.get("ProcessId")))
    return pids


def kill(pid: str) -> bool:
    result = subprocess.run(["taskkill", "/PID", pid, "/F"],
                            capture_output=True, text=True)
    return result.returncode == 0


def neutralize_cycle() -> bool:
    """Kill one probe cycle: probe python first, then its server. True when
    anything was killed."""
    acted = False
    for pid in probe_pids():
        log(f"killing probe/server python {pid} (ungraded = unavailable, never counts)")
        acted = kill(pid) or acted
    server_pid = listening_pid(SMOKE_PORT)
    if server_pid:
        log(f"killing probe server {server_pid} on :{SMOKE_PORT}")
        acted = kill(server_pid) or acted
    return acted


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    log("babysitter on watch: neutralizing smoke-probe cycles for the old "
        "pipeline (trainer comes first; probes are telemetry only)")
    while True:
        if verdict_is_fresh():
            log("fresh verdict exists - the run is over, standing down")
            return 0
        if not old_pipeline_alive():
            log("old pipeline gone - finish_pipeline owns the tail, standing down")
            return 0
        neutralize_cycle()
        time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())
