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
import os
import subprocess
import sys
from pathlib import Path

LOG_DIR = Path("D:/hwk-data")
# Children spawned here set this so a script with its own self-detach guard
# (soup_pipeline.self_detach) knows it is ALREADY detached and must not
# re-spawn itself a second time.
ENV_MARKER = "HWK_DETACHED"


def detached_creationflags() -> int:
    """DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP + CREATE_BREAKAWAY_FROM_JOB.

    2026-09-12 16:29 lesson (attempt 8): the pipeline launched by the
    scheduled task "HWK SoupPipeline" died in its first minute. The task's
    python runs inside a Task Scheduler JOB OBJECT with kill-on-job-close;
    our DETACHED child still INHERITED that job, so when the parent exited 0
    (the self-detach design: parent returns, child runs), the scheduler
    closed the job and the kernel killed every process in it - the fresh
    pipeline died WITH its parent's success, logging only "starting Soup
    server". CREATE_BREAKAWAY_FROM_JOB lets the child leave the job at birth
    (it requires JOB_OBJECT_LIMIT_BREAKAWAY_OK on the job; where that is not
    set, CreateProcess refuses the flag and the caller must retry without
    it - see spawn_detached's fallback)."""
    if sys.platform != "win32":
        return 0
    return (subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB)


def _plain_detached_flags() -> int:
    """The pre-2026-09-12-16:29 flag set (no job breakaway)."""
    if sys.platform != "win32":
        return 0
    return subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP


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
    """Spawn command detached; returns the child pid.

    Tries the breakaway spawn first (survives a scheduled-task / CI job
    closing over the parent - the 16:29 attempt-8 kill); when the job
    refuses breakaway (no JOB_OBJECT_LIMIT_BREAKAWAY_OK), retries with the
    plain detached flag set so the launch still works."""
    out_path = LOG_DIR / f"{log_base}.log"
    err_path = LOG_DIR / f"{log_base}.log.err"
    try:
        return _spawn(command, out_path, err_path, cwd,
                      detached_creationflags())
    except OSError as exc:
        if sys.platform != "win32":
            raise
        print(f"[launch_detached] breakaway refused ({exc}); retrying "
              "without it - child may die if the parent's job closes",
              file=sys.stderr, flush=True)
        return _spawn(command, out_path, err_path, cwd,
                      _plain_detached_flags())


def _spawn(command: list[str], out_path: Path, err_path: Path, cwd: Path,
           creationflags: int) -> int:
    with out_path.open("a", encoding="utf-8") as out, \
            err_path.open("a", encoding="utf-8") as err:
        child = subprocess.Popen(  # noqa: S603 - owner-controlled command
            command,
            stdout=out, stderr=err, stdin=subprocess.DEVNULL,
            cwd=str(cwd), creationflags=creationflags,
            close_fds=True,
            env={**os.environ, ENV_MARKER: "1"},
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
