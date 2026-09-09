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


def wait_vram_clear(timeout_s: int = 300) -> None:
    """Poll until used VRAM is desktop-idle (<1.5 GB) or timeout.

    On PROMOTE the pipeline leaves the promoted adapter server running
    (by design - it IS the live brain), which holds several GB. Starting
    Phase B's trainer on top of it would OOM (lesson 2026-09-09 09:50).
    """
    deadline = time.monotonic() + timeout_s
    last = -1
    while time.monotonic() < deadline:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=30,
            ).stdout.strip().splitlines()
            last = int(out[-1]) if out else 1 << 30
        except (OSError, ValueError, subprocess.TimeoutExpired):
            last = 1 << 30
        if last < 1500:
            log(f"VRAM clear ({last} MiB) - safe for Phase B")
            return
        time.sleep(10)
    log(f"VRAM still busy after {timeout_s}s ({last} MiB) - Phase B may OOM; "
        "if the promoted server is up, that is expected - stop it or move "
        "Phase B to a free window")


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

    # Standing owner rule (2026-09-08): snapshot the Obsidian brain vault to
    # X: before every GPU training run. Non-fatal on failure, like the
    # caretaker's version.
    try:
        vault = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_brain_vault.py"),
             "--out", "X:/hwk-backups/aali-brain-vault"],
            capture_output=True, text=True, timeout=120,
        )
        log(f"vault export -> X:/hwk-backups/aali-brain-vault "
            f"(exit {vault.returncode})")
    except Exception as exc:  # noqa: BLE001
        log(f"vault export failed (non-fatal): {exc}")

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
    # Wait for real VRAM release: a promoted server intentionally stays up.
    wait_vram_clear()
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
