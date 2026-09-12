"""Generic detached launcher: run a command with NO console, survive closes.

2026-09-12 12:20 lesson: every process living in a visible console dies with
it - a console Ctrl+C/close event swept the soup pipeline, the trainer and
the smoke probe at step 1270/3804. Long jobs must never be console-bound.

This launcher spawns its command fully detached (DETACHED_PROCESS +
CREATE_NEW_PROCESS_GROUP: no console to receive close/Ctrl+C events, own
process group), redirects stdout/stderr to append-mode files under
D:/hwk-data/, and returns immediately. The child keeps running no matter
what happens to the caller's window.

Usage (from .bat or shell):
  python scripts/launch_detached.py --log context_training -- \
      .venv/Scripts/python.exe train_scratch.py --data ... --resume

  --log <name>   log basename: D:/hwk-data/<name>.log + <name>.log.err
                 (default: detached_run)

Exit codes: 0 = spawned (child pid printed), 2 = bad usage / spawn failed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

LOG_DIR = Path("D:/hwk-data")


def build_command(argv: list[str]) -> list[str]:
    """Resolve the executable to an absolute path (the caller's cwd decides,
    not the child's) and return the spawn-ready command list."""
    command = list(argv)
    if not command:
        raise ValueError("no command after '--'")
    exe = Path(command[0])
    command[0] = str(exe if exe.is_absolute() else exe.resolve())
    return command


def spawn_detached(command: list[str], log_base: str, cwd: Path) -> int:
    """Spawn command detached; returns the child pid."""
    out_path = LOG_DIR / f"{log_base}.log"
    err_path = LOG_DIR / f"{log_base}.log.err"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with out_path.open("a", encoding="utf-8") as out, \
            err_path.open("a", encoding="utf-8") as err:
        child = subprocess.Popen(  # noqa: S603 - owner-controlled command
            command,
            stdout=out, stderr=err, stdin=subprocess.DEVNULL,
            cwd=str(cwd), creationflags=creationflags,
            close_fds=True,
        )
    print(f"[launch_detached] pid {child.pid} -> {out_path} (err: {err_path.name})",
          flush=True)
    return child.pid


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a command fully detached")
    parser.add_argument("--log", default="detached_run",
                        help="log basename under D:/hwk-data (no extension)")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="command to run, after '--'")
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    try:
        command = build_command(command)
    except ValueError as exc:
        print(f"launch_detached: {exc}", file=sys.stderr, flush=True)
        return 2
    try:
        spawn_detached(command, args.log, cwd=Path(__file__).resolve().parents[1])
        return 0
    except OSError as exc:
        print(f"launch_detached: spawn failed: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
