"""Aali Monitor — desktop watcher for Aali's long jobs (2026-09-25).

Usage:
    python -m aali_monitor              # GUI (pywebview + tray)
    python -m aali_monitor --headless   # console-only alerts
    python -m aali_monitor --once       # one snapshot, print, exit

Reads the real D:/hwk-data logs; content-free output only.
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aali_monitor", description="مراقب أعمال آلي — Aali Monitor")
    parser.add_argument("--headless", action="store_true",
                        help="بدون نافذة — تنبيهات على stderr فقط")
    parser.add_argument("--once", action="store_true",
                        help="لقطة واحدة ثم خروج")
    parser.add_argument("--poll-seconds", type=int, default=20,
                        help="فاصل التحديث (افتراضي 20)")
    parser.add_argument("--data-dir", default=None,
                        help="جذر السجلات (افتراضي D:/hwk-data — للاختبار)")
    args = parser.parse_args(argv)

    if args.data_dir:
        from pathlib import Path
        from . import core
        core.DATA_DIR = Path(args.data_dir)
        core.SERVER_LOG = core.DATA_DIR / "aali_server.log"
        core.PHASE_D_LOG = core.DATA_DIR / "phase_d_train.log"
        core.PIPELINE_LOG = core.DATA_DIR / "soup_pipeline.log"

    if args.once:
        from . import core
        snap = core.snapshot()
        print(json.dumps(snap, ensure_ascii=False, indent=2, default=str))
        return 0

    from .shell import run_monitor
    return run_monitor(headless=args.headless,
                       poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
