"""PyInstaller entry point for the آلي Node exe.

A thin shim over aali_node.daemon.main — the SAME argv contract as the
headless source run (`python -m aali_node …`), so every documented flag
keeps working in the packaged exe:

    aali-node.exe --hub ws://hub:8080 --token <JWT>
    aali-node.exe --shell --hub ws://hub:8080 --token <JWT>
    aali-node.exe --activate-update

Not imported by anything at runtime — this file exists so PyInstaller's
static analysis has a stable, dependency-light module to start from.
"""
from __future__ import annotations

import sys
from pathlib import Path

# PyInstaller analyzes the entry module's imports; file_agent (sandbox) and
# aali_hub (updater's verify_manifest) live OUTSIDE the repo root in
# file-agent/, so make both resolvable for the analysis pass AND at runtime
# before importing the daemon.
_HERE = Path(__file__).resolve().parent
for _extra in (str(_HERE), str(_HERE / "file-agent")):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

from aali_node.daemon import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
