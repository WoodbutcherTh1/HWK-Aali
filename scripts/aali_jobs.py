"""Live jobs snapshot — one source for CLI /jobs and the desktop pill.

Owner request (2026-09-23): see in the CLI and the desktop app which jobs
are running right now, like the Freebuff screen. This module composes the
status_digest board's read-only sections into a single snapshot dict.
Deliberately does NOT call status_digest.phase_b() — that one writes a
pace-state file on every call, and a UI poll running every few seconds must
stay read-only.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import status_digest as sd  # noqa: E402


def snapshot() -> dict[str, Any]:
    """Collect every long-running job's state in one read-only pass."""
    rows: list[dict[str, str]] = []

    def add(section: str, results: list[tuple[str, str]]) -> None:
        for icon, text in results:
            rows.append({"section": section, "icon": icon, "text": text})

    add("GPU", [(sd.gpu_summary(), "")] if sd.gpu_summary() else [])
    add("Phase D", sd.phase_d())
    add("Caretaker", sd.caretaker())
    add("Pipeline", sd.pipeline())
    add("Pi-CI", sd.pi_ci())
    add("Disks", sd.disks())
    alerts = sum(1 for r in rows if r["icon"] == "⚠️")
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "alerts": alerts,
        "rows": rows,
    }


def render_text(snap: dict[str, Any] | None = None) -> list[str]:
    """Plain-text lines (CLI paints them; tests assert on them)."""
    snap = snap or snapshot()
    lines = [f"Jobs snapshot — {snap['generated']} "
             f"({snap['alerts']} alert(s))", "-" * 46]
    section = None
    for row in snap["rows"]:
        if row["section"] != section:
            section = row["section"]
            lines.append(f"[{section}]")
        text = f"  {row['icon']} {row['text']}" if row["text"] else f"  {row['icon']}"
        lines.append(text)
    return lines


if __name__ == "__main__":
    print("\n".join(render_text()))
