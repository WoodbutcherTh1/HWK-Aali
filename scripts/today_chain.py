# -*- coding: utf-8 -*-
"""Morning chain (2026-09-09): graduation pipeline first, then Phase B resume.

Freebuff's restart killed the overnight caretaker + pipeline mid-flight (and
the auto-respawn ran TWO soup.exe copies into an OOM). The GPU is free now,
so the fastest honest path to promotion is running the soup graduation
pipeline NOW, and resuming Phase B (context extension, checkpointed at step
35,500/100,000) the moment the pipeline releases the GPU.

Strictly serial: stage 2 starts only after stage 1's process exits, and only
if stage 1 succeeded - never two trainers on the 8GB card at once.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NIGHT_LOG = Path("D:/hwk-data/soup_pipeline_night.log")
CHAIN_LOG = Path("D:/hwk-data/morning_chain.log")


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    try:
        with CHAIN_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def run_stage(name: str, cmd: list[str], out: Path) -> int:
    log(f"=== stage start: {name} ===")
    with out.open("a", encoding="utf-8") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(ROOT))
    log(f"=== stage end: {name} (exit {proc.returncode}) ===")
    return proc.returncode


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    log("morning chain start: graduation pipeline -> Phase B resume (strictly serial)")

    code = run_stage(
        "graduation pipeline",
        [str(ROOT / ".venv" / "Scripts" / "python.exe"),
         str(ROOT / "scripts" / "soup_pipeline.py")],
        NIGHT_LOG,
    )
    if code != 0:
        log("pipeline failed - NOT starting Phase B on a possibly busy GPU; "
            "check D:/hwk-data/soup_pipeline_last_stage.txt and relaunch today_chain.py")
        return code

    # Phase B resume: extend_context.bat reuses the context-4k checkpoint
    # (train_scratch --resume continues at step 35,500) and mirrors to X:.
    # Give the driver a minute to release VRAM before loading again.
    time.sleep(60)
    code = run_stage(
        "Phase B resume (context 4096)",
        [r"C:\Windows\System32\cmd.exe", "/c", str(ROOT / "scripts" / "extend_context.bat")],
        CHAIN_LOG,
    )
    log(f"morning chain done (Phase B exit {code})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
