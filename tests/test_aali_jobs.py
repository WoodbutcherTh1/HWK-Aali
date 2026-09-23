"""Tests for scripts/aali_jobs.py — the shared live-jobs snapshot.

CPU-only, tmp redirects: no real D:/hwk-data files are touched.

Run:
    .venv/Scripts/python -m pytest tests/test_aali_jobs.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import aali_jobs  # noqa: E402
import status_digest as sd  # noqa: E402


def test_snapshot_counts_alerts_and_sections(tmp_path: Path) -> None:
    old_data = sd.DATA
    sd.DATA = tmp_path
    try:
        (tmp_path / "phase_d_train_stage4.log").write_text(
            "step=100 eval_loss=2.0\n", encoding="utf-8")
        snap = aali_jobs.snapshot()
    finally:
        sd.DATA = old_data
    sections = {row["section"] for row in snap["rows"]}
    assert "Phase D" in sections
    assert "Pi-CI" in sections
    assert snap["alerts"] >= 0
    assert all(set(r) == {"section", "icon", "text"} for r in snap["rows"])


def test_render_text_groups_by_section(tmp_path: Path) -> None:
    snap = {
        "generated": "2026-09-23 12:00:00", "alerts": 0,
        "rows": [
            {"section": "GPU", "icon": "🔥", "text": "97 %"},
            {"section": "Phase D", "icon": "✅", "text": "stage 4 training"},
            {"section": "Phase D", "icon": "✅", "text": "weights saved"},
        ],
    }
    lines = aali_jobs.render_text(snap)
    joined = "\n".join(lines)
    assert "[GPU]" in joined and "[Phase D]" in joined
    assert lines.count("  ✅ stage 4 training") == 1
    assert "Jobs snapshot" in lines[0]
