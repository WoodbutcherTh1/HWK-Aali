"""Extract Hebrew Wikipedia dumps to JSON with wikiextractor (Phase 2.2).

Runs the D:/hwk-tools/hebrew-venv wikiextractor per verified dump part into
D:/hwk-data/hebrew/extracted/hewiki/part<N>/ (separate dirs = collision-free
and re-runnable per part). Content-free progress lines go to
D:/hwk-data/hebrew/EXTRACT_LOG.md. CPU-only.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

RAW = Path("D:/hwk-data/hebrew/raw/hewiki")
OUT = Path("D:/hwk-data/hebrew/extracted/hewiki")
LOG = Path("D:/hwk-data/hebrew/EXTRACT_LOG.md")
VENV_PY = Path("D:/hwk-tools/hebrew-venv/Scripts/python.exe")


def log(message: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {message}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def main() -> None:
    parts = sorted(RAW.glob("*.xml-*.bz2"))
    if not parts:
        raise SystemExit("no verified dump parts found under raw/hewiki/")
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"extraction start: {len(parts)} parts")

    for index, part in enumerate(parts, 1):
        part_out = OUT / f"part{index}"
        marker = part_out / "_done.marker"
        if marker.exists():
            log(f"part{index}: already extracted (marker present) - skipping")
            continue
        part_out.mkdir(parents=True, exist_ok=True)
        started = time.time()
        log(f"part{index}: extracting {part.name} ({part.stat().st_size} bytes)")
        completed = subprocess.run(
            [str(VENV_PY), "-m", "wikiextractor.WikiExtractor",
             "--json", "--processes", "6", "--quiet",
             "--output", str(part_out), str(part)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        minutes = (time.time() - started) / 60
        if completed.returncode != 0:
            log(f"part{index}: FAILED exit {completed.returncode} after {minutes:.1f} min")
            tail = (completed.stderr or "")[-500:]
            log(f"part{index}: stderr tail: {tail}")
            raise SystemExit(1)
        marker.write_text("ok\n", encoding="utf-8")
        files = sorted(part_out.rglob("*.json")) if not any(part_out.rglob("*")) else None
        log(f"part{index}: done in {minutes:.1f} min")

    log("extraction DONE: all parts")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
