"""Checkpoint watchdog: archive every Phase A checkpoint save to a backup dir.

The trainer overwrites checkpoint.pt / trainer-state.pt / final.pt in place
every --save-steps, so a crash between saves loses progress. This watcher
tails D:/hwk-data/training.log and, on every "checkpoint saved: ... step=N"
line, copies the three files to <out>/step_<N>/ and validates the copy by
reading its trainer-state step. Keeps the newest --keep snapshots; prunes
older ones. Exits when training.log reports "done:".

Usage:
  .venv/Scripts/python.exe scripts/checkpoint_watchdog.py
      [--watch D:/hwk-data/training.log] [--model-dir D:/hwk-models/scratch]
      [--out D:/hwk-models/scratch-backups] [--keep 12] [--idle-timeout 3600]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAVE_RE = re.compile(r"checkpoint saved: .*?step=(\d+)")
DONE_RE = re.compile(r"^done: ")


def snapshot_step(model_dir: Path, out_dir: Path, step: int) -> tuple[bool, str]:
    destination = out_dir / f"step_{step}"
    if destination.exists():
        return True, f"step_{step} already archived"
    destination.mkdir(parents=True, exist_ok=True)
    try:
        for name in ("checkpoint.pt", "trainer-state.pt", "final.pt"):
            source = model_dir / name
            if not source.exists():
                raise FileNotFoundError(f"missing {source}")
            shutil.copy2(source, destination / name)
        # Validate: the copy must load and report the expected step.
        import torch

        state = torch.load(destination / "trainer-state.pt", map_location="cpu", weights_only=False)
        saved_step = int(state.get("step", -1))
        if saved_step != step:
            raise ValueError(f"archived trainer-state says step {saved_step}, expected {step}")
        return True, f"archived + validated step_{step}"
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(destination, ignore_errors=True)
        return False, f"archive failed for step {step}: {exc}"


def prune(out_dir: Path, keep: int) -> None:
    snapshots = sorted(out_dir.glob("step_*"), key=lambda p: int(p.name.split("_")[1]))
    for stale in snapshots[:-keep] if len(snapshots) > keep else []:
        shutil.rmtree(stale, ignore_errors=True)
        print(f"pruned {stale.name}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive Phase A checkpoints as they are saved.")
    parser.add_argument("--watch", default="D:/hwk-data/training.log")
    parser.add_argument("--model-dir", default="D:/hwk-models/scratch")
    parser.add_argument("--out", default="D:/hwk-models/scratch-backups")
    parser.add_argument("--keep", type=int, default=12)
    parser.add_argument("--idle-timeout", type=int, default=3600, help="Exit if no new log lines for this many seconds.")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    log_path = Path(args.watch)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"watching {log_path} -> {out_dir} (keep {args.keep})", flush=True)
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)  # start at the tail; existing snapshots stay as-is
        last_line_time = time.time()
        while True:
            line = handle.readline()
            if not line:
                time.sleep(2.0)
                if time.time() - last_line_time > args.idle_timeout:
                    print("idle timeout: no log activity, exiting (checkpoints already archived)", flush=True)
                    return
                continue
            last_line_time = time.time()
            if DONE_RE.search(line):
                print("training run finished - watchdog exiting", flush=True)
                return
            match = SAVE_RE.search(line)
            if match:
                step = int(match.group(1))
                ok, message = snapshot_step(Path(args.model_dir), out_dir, step)
                print(f"[watchdog] {message}", flush=True)
                if ok:
                    prune(out_dir, args.keep)


if __name__ == "__main__":
    main()
