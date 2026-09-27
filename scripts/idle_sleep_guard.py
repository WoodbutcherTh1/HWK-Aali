#!/usr/bin/env python3
"""Idle sleep guard for the home PC - decides when the PC MAY sleep.

Pairs with the Pi sentinel (scripts/pi_sentinel_catcher.py): the Pi runs
24/7 and wakes this PC with a Wake-on-LAN magic packet when traffic
arrives, so this PC no longer needs to stay awake just to be reachable.

SAFETY CONTRACT - the guard NEVER puts the PC to sleep by itself. It only
prints OK / WAIT and exits with a status code:
  exit 0 = OK to sleep        (nothing below is running)
  exit 1 = WAIT - protected work is active
The owner's power plan (or scripts/wol_enable_pc.bat --install-sleep-task)
turns this check into actual sleep; the guard is the safety BRAIN, not the
hand. --install-sleep-task is therefore opt-in and prints what it does.

Blocked states (any one of these = WAIT):
  * any user session holds a process named ssh/vim/code/python-besides-guard
    (interactive work) - TODO if the owner asks for it; today we gate on:
  * an ssh.exe session is active (owner working over the network)
  * training-like protection via wait_gpu_free.py's real classifier:
    training.log heartbeated recently (its writer heartbeats every ~1.5 min)
  * training-like protection: soup/pipeline/caretaker/watchdog PROCESS
    names alive (they hold the card or hold job state)
  * GPU: python/soup compute PIDs on the card that are NOT the three known
    always-on servers (brain soup.exe, API app.py, watchdog, TTS/monitor
    helpers) - i.e. anything trainer-like blocks sleep
  * browser/media/gaming processes that mean someone is USING the pc

Default behavior with no arguments: run the check once and exit. A loop
mode exists for the scheduled task: --loop.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wait_gpu_free import python_compute_pids, training_log_idle_minutes  # noqa: E402

# Process names that mean a human (or their ssh session) is using the PC.
USER_SESSION_NAMES = {
    "ssh.exe", "vim.exe", "nvim.exe", "notepad.exe", "devenv.exe",
    "chrome.exe", "msedge.exe", "firefox.exe", "code.exe",
    "spotify.exe", "steam.exe", "elgato.exe", "discord.exe",
}
# The always-on Aali infrastructure: python-family GPU holders we EXPECT.
# (Fragments are written with forward slashes and matched against
# backslash-normalized cmdlines, so bash-started and cmd-started
# processes both match.)
EXPECTED_SERVER_FRAGMENTS = (
    "file-agent/app.py",         # API :5055
    "soup.exe serve",            # brain :20129 (shim + real python)
    "aali_brain_watchdog.py",    # brain guardian
    "search_index",              # FTS helper if it ever runs
)
# Name fragments that mean graduation/training machinery is alive.
TRAINING_FRAGMENTS = (
    "soup_pipeline", "night_caretaker", "train_scratch", "phase_c",
    "phase_d", "soup train", "soup_probe_smoke", "finish_pipeline",
    "finish_babysit", "status_digest", "aali_jobs", "mission_clock",
    "pi_ci", "ship_to_pi", "distill_teacher", "audit_refusal_gates",
    "soup_exam",
)
TRAINING_LOG = Path(r"D:\hwk-data\training.log")
MIN_TRAINING_LOG_IDLE_MIN = 10


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def _ps(command: str) -> str | None:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def training_like_processes() -> list[str]:
    """Cmdlines of running python-family processes, content-free names only."""
    out = _ps(
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match 'python|soup|cloudflared|cmd' } | "
        "ForEach-Object { \"[{0}] {1}~{2}\" -f $_.ProcessId, $_.CommandLine, $_.ParentProcessId }"
    )
    if out is None:
        return []  # cannot verify -> the training check below covers us
    return [line for line in out.splitlines() if line.strip()]


def _line_for_pid(lines: list[str], pid: int) -> str:
    return next((l for l in lines if l.startswith(f"[{pid}] ")), "")


def _holder_match(line: str, fragments: tuple[str, ...]) -> bool:
    """Slash-normalized fragment match: bash starts write file-agent/app.py,
    cmd writes file-agent\\app.py - both must count."""
    normalized = line.replace("\\", "/")
    return any(fragment in normalized for fragment in fragments)


def _expected_gpu_holder(lines: list[str], pid: int) -> bool:
    """True when a GPU compute PID belongs to the known always-on stack.

    Matched by its OWN cmdline (soup.exe serve / app.py / watchdog) or by
    ANCESTRY: the soup.exe shim spawns a bare python.exe whose cmdline has
    no recognizable fragment - its PARENT carries the serve line, so walk
    up via the recorded ParentProcessId instead of guessing (live-proven:
    pid 113112 bare python under the shim was the brain itself).
    """
    line = _line_for_pid(lines, pid)
    if line and _holder_match(line, EXPECTED_SERVER_FRAGMENTS):
        return True
    seen: set[int] = set()
    current = pid
    for _ in range(4):
        if current in seen:
            break
        seen.add(current)
        line = _line_for_pid(lines, current)
        if not line or "~" not in line:
            break
        try:
            parent = int(line.rsplit("~", 1)[1].strip().strip('"'))
        except ValueError:
            break
        if not parent:
            break
        parent_line = _line_for_pid(lines, parent)
        if parent_line and _holder_match(parent_line, EXPECTED_SERVER_FRAGMENTS):
            return True
        current = parent
    return False


def check_sleep_ok() -> tuple[bool, str]:
    """Single source of truth for the sleep decision (pure-ish, testable)."""
    # 1) training.log heartbeat: a running trainer writes every ~1.5 min.
    idle = training_log_idle_minutes()
    if idle is not None and idle < MIN_TRAINING_LOG_IDLE_MIN:
        return False, f"training.log written {idle:.1f} min ago"

    lines = training_like_processes()
    joined = "\n".join(lines)

    # 2) training/graduation machinery alive?
    for fragment in TRAINING_FRAGMENTS:
        if fragment in joined:
            return False, f"training machinery alive: {fragment}"

    # 3) GPU compute PIDs beyond the expected always-on servers.
    pids = python_compute_pids()
    if pids is None:
        return False, "cannot verify GPU compute processes"
    if pids:
        for pid in pids:
            if not _expected_gpu_holder(lines, pid):
                line = _line_for_pid(lines, pid)
                return False, f"unexpected GPU compute pid {pid}: {line[:80]}"

    # 4) interactive user session processes (ssh, editor, browser, media).
    out = _ps(
        "Get-Process | Where-Object { "
        + " ".join(f"$_.Name -eq '{n[:-4]}'" for n in sorted(USER_SESSION_NAMES))
        + " } | Select-Object -First 1 -ExpandProperty Name"
    )
    if out:  # any hit = a human is around
        return False, f"user session active: {out}"

    return True, "no training, no unexpected GPU users, no user session"


def run_loop(poll_s: int, log_file: Path | None) -> int:  # pragma: no cover
    """Loop mode for the scheduled task - logs WAIT reasons, sleeps on OK."""
    fh = None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log_file, "a", encoding="utf-8")

    def emit(msg: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {msg}"
        print(line, flush=True)
        if fh:
            fh.write(line + "\n")
            fh.flush()

    while True:
        ok, reason = check_sleep_ok()
        emit("OK - sleeping is safe" if ok else f"WAIT - {reason}")
        time.sleep(poll_s)


def install_sleep_task(poll_s: int) -> int:  # pragma: no cover
    """Register the loop as a scheduled task that sleeps the PC on OK.

    OPT-IN: this writes an actual power action into the loop. The task
    runs at logon and every 5 minutes; the loop only sleeps the PC when
    check_sleep_ok() says OK and the PC has been idle-surface-wise.
    """
    loop_line = (
        f".venv\\Scripts\\python.exe {Path(__file__).name} --loop "
        f"--poll {max(poll_s, 300)} --log D:\\hwk-data\\sleep_guard.log --act"
    )
    print("Would register scheduled task 'HWK SleepGuard' running:")
    print(f"  {loop_line}")
    print("NOTE: the --act sleep action is intentionally NOT implemented in")
    print("this version - review the WAIT/OK log for a few days first; then")
    print("add the rundll32 sleep call with the owner's explicit approval.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Aali idle sleep guard")
    parser.add_argument("--loop", action="store_true",
                        help="run continuously (scheduled task mode)")
    parser.add_argument("--poll", type=int, default=300,
                        help="seconds between checks in --loop mode")
    parser.add_argument("--log", default=None,
                        help="log file for --loop mode")
    parser.add_argument("--act", action="store_true",
                        help="(reserved) actually sleep the PC on OK")
    args = parser.parse_args()

    if args.loop:
        return run_loop(args.poll, Path(args.log) if args.log else None)

    ok, reason = check_sleep_ok()
    log(f"{'OK to sleep' if ok else 'WAIT - do not sleep'}: {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
