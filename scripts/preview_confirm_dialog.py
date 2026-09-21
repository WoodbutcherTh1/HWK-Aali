"""Eyeball the Aali confirm dialog without running the daemon.

Pops the REAL styled dialog on screen (the same one the sandbox shows for
dangerous tool calls) and prints what the sandbox would have received.

Usage:
    python scripts/preview_confirm_dialog.py               # delete_file
    python scripts/preview_confirm_dialog.py run_command   # or any of:
    # delete_file / run_command / move_file / write_file / machine_ops

No state is touched — this is a pure window preview.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows consoles default to cp1252; Arabic sample paths would die there
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from aali_node.confirm import (  # noqa: E402
    CONFIRM_TIMEOUT_SEC, _IDYES, _TITLE, _describe, _show_confirm_dialog)

SAMPLES = {
    "delete_file": {"path": "مجلد/تقرير-مهم.txt"},
    "run_command": {"command": "pip install requests"},
    "move_file": {"source": "a.txt", "destination": "archive/a.txt"},
    "write_file": {"path": "config.json"},
    "machine_ops": {"action": "restart", "target": "aali-node"},
}


def main() -> int:
    tool = sys.argv[1] if len(sys.argv) > 1 else "delete_file"
    args = SAMPLES.get(tool, {"path": "example.txt"})
    ar, en = _describe(tool, args)
    print(f"showing dialog for: {tool} {args!r}")
    print("(no click within 60s = auto-deny, exactly like production)\n")
    result = _show_confirm_dialog(_TITLE, ar, en, CONFIRM_TIMEOUT_SEC)
    verdict = "ALLOWED (IDYES)" if result == _IDYES else "DENIED"
    print(f"sandbox would receive: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
