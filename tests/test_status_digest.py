"""Tests for the status digest's alert semantics (2026-09-12 false alarms).

The board must not cry wolf: a caretaker that ENDED ITS DUTY normally
("going to sleep" as the last log line) is not STALLED, and a pipeline
mid-training (log active but the start marker scrolled past the tail
window) is RUNNING, not a mystery verdict. CPU-only, tmp files.

Run:
    .venv/Scripts/python -m pytest tests/test_status_digest.py -q
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import status_digest as sd  # noqa: E402


def _write(path: Path, text: str, age_s: float = 0.0) -> None:
    path.write_text(text, encoding="utf-8")
    if age_s:
        past = time.time() - age_s
        os.utime(path, (past, past))


def _stamp(age_s: float) -> str:
    """A pipeline-format UTC stamp age_s seconds old (the digest parses
    stamps, not file mtimes - pytest junk has no stamp)."""
    return f"[{datetime.now(timezone.utc) - timedelta(seconds=age_s):%Y-%m-%d %H:%M:%S}]"


# ---------------------------------------------------------------------------
# caretaker(): end-of-duty is not a stall (2026-09-12 false alarm)
# ---------------------------------------------------------------------------

def test_caretaker_sleep_line_is_normal_end_not_stall(tmp_path: Path) -> None:
    log = tmp_path / "caretaker.log"
    _write(log, "[23:58:48] morning report written\n"
                "[23:58:49] === night caretaker going to sleep ===\n",
           age_s=10 * 3600)
    icon, msg = _care(log)
    assert icon == "✅"
    assert "duty ended normally" in msg
    assert "STALLED" not in msg


def _care(log: Path) -> tuple[str, str]:
    sd.CARE_LOG = log
    rows = sd.caretaker()
    assert len(rows) == 1
    return rows[0]


def test_caretaker_stale_mid_duty_log_is_a_stall(tmp_path: Path) -> None:
    log = tmp_path / "caretaker.log"
    _write(log, "[10:00:00] Phase B alive: step 100, eval 3.2\n",
           age_s=sd.STALL_SEC + 60)
    icon, msg = _care(log)
    assert icon == "⚠️"
    assert "STALLED" in msg and "relaunch" in msg


def test_caretaker_fresh_log_is_on_duty(tmp_path: Path) -> None:
    log = tmp_path / "caretaker.log"
    _write(log, "[13:00:00] Phase B alive: step 100, eval 3.2\n", age_s=120)
    icon, msg = _care(log)
    assert icon == "✅"
    assert "on duty" in msg


def test_caretaker_missing_log_alerts(tmp_path: Path) -> None:
    icon, msg = _care(tmp_path / "missing.log")
    assert icon == "⚠️"
    assert "no caretaker log" in msg


# ---------------------------------------------------------------------------
# pipeline(): mid-training activity is RUNNING; INCOMPLETE is a named verdict
# ---------------------------------------------------------------------------

def _pipeline(soup_log: Path, report: Path) -> tuple[str, str]:
    sd.SOUP_LOG = soup_log
    sd.REPORT = report
    rows = sd.pipeline()
    assert len(rows) == 1
    return rows[0]


def test_pipeline_running_on_recent_log_without_start_marker(tmp_path: Path) -> None:
    # mid-training: the "start" marker scrolled past the tail window, but the
    # smoke watcher writes every ~6.5 min - a fresh STAMPED line proves it runs
    soup_log = tmp_path / "soup_pipeline.log"
    report = tmp_path / "resft_pipeline_report.md"
    _write(soup_log, f"{_stamp(25 * 60)} smoke probe [midtrain]: launching\n"
                     f"{_stamp(2 * 60)} smoke probe [midtrain] score: 1/6\n")
    _write(report, "**Verdict: INCOMPLETE**\n", age_s=10 * 3600)
    icon, msg = _pipeline(soup_log, report)
    assert icon == "🔄"
    assert "RUNNING" in msg


def test_pipeline_incomplete_report_names_the_retry(tmp_path: Path) -> None:
    soup_log = tmp_path / "soup_pipeline.log"
    report = tmp_path / "resft_pipeline_report.md"
    _write(soup_log, f"{_stamp(9 * 3600)} === soup pipeline start ===\n")
    _write(report, "**Verdict: INCOMPLETE - a stage failed**\n", age_s=3600)
    icon, msg = _pipeline(soup_log, report)
    assert icon == "⚠️"
    assert "INCOMPLETE" in msg and "auto-retries" in msg


def test_pipeline_pytest_junk_cannot_fake_running(tmp_path: Path) -> None:
    # a fresh pytest write (no stamp) must not read as pipeline activity:
    # the newest STAMPED line is 9 h old, so the board must look at the
    # report instead of the junk
    soup_log = tmp_path / "soup_pipeline.log"
    report = tmp_path / "resft_pipeline_report.md"
    _write(soup_log, f"{_stamp(9 * 3600)} === soup pipeline start ===\n"
                     "C:\\Temp\\pytest-of-HmamK\\x verdict: PROMOTE\n")
    _write(report, "**Verdict: NO-GO**\n", age_s=600)
    icon, msg = _pipeline(soup_log, report)
    assert icon == "⚠️"  # the fresh verdict comes from the REPORT, not pytest lines
    assert "NO-GO" in msg


def test_pipeline_promote_is_graduation(tmp_path: Path) -> None:
    soup_log = tmp_path / "soup_pipeline.log"
    report = tmp_path / "resft_pipeline_report.md"
    _write(soup_log, f"{_stamp(9 * 3600)} === soup pipeline start ===\n")
    _write(report, "**Verdict: PROMOTE**\n", age_s=600)
    icon, msg = _pipeline(soup_log, report)
    assert icon == "🎉"
    assert "PROMOTE" in msg


def test_pipeline_fresh_start_but_silent_log_is_stalled(tmp_path: Path) -> None:
    # 2026-09-12 17:03 false alarm: the reboot killed attempt 8 one minute
    # after "=== soup pipeline start ===" - a young start marker over a
    # silent log is a DEAD run, not a RUNNING one.
    soup_log = tmp_path / "soup_pipeline.log"
    report = tmp_path / "resft_pipeline_report.md"
    _write(soup_log, f"{_stamp(34 * 60)} === soup pipeline start ===\n"
                     f"{_stamp(34 * 60)} starting Soup server on port 20129\n")
    _write(report, "**Verdict: INCOMPLETE**\n", age_s=10 * 3600)
    icon, msg = _pipeline(soup_log, report)
    assert icon == "⚠️"
    assert "STALLED" in msg and "relaunch" in msg
    assert "RUNNING" not in msg


def test_pipeline_fresh_start_with_recent_stage_line_is_running(
        tmp_path: Path) -> None:
    # a genuinely live run writes stage lines right after the start marker
    soup_log = tmp_path / "soup_pipeline.log"
    report = tmp_path / "resft_pipeline_report.md"
    _write(soup_log, f"{_stamp(3 * 60)} === soup pipeline start ===\n"
                     f"{_stamp(60)} starting Soup server on port 20129\n")
    _write(report, "**Verdict: INCOMPLETE**\n", age_s=10 * 3600)
    icon, msg = _pipeline(soup_log, report)
    assert icon == "🔄"
    assert "RUNNING" in msg


def test_pipeline_no_go_alerts(tmp_path: Path) -> None:
    soup_log = tmp_path / "soup_pipeline.log"
    report = tmp_path / "resft_pipeline_report.md"
    _write(soup_log, f"{_stamp(9 * 3600)} === soup pipeline start ===\n")
    _write(report, "**Verdict: NO-GO for promotion (tie)**\n", age_s=600)
    icon, msg = _pipeline(soup_log, report)
    assert icon == "⚠️"
    assert "NO-GO" in msg


def test_pipeline_idle_when_nothing_is_fresh(tmp_path: Path) -> None:
    soup_log = tmp_path / "soup_pipeline.log"
    report = tmp_path / "resft_pipeline_report.md"
    _write(soup_log, f"{_stamp(9 * 3600)} === soup pipeline start ===\n")
    _write(report, "**Verdict: PROMOTE**\n", age_s=60 * 3600)
    icon, msg = _pipeline(soup_log, report)
    assert icon == "💤"
    assert "idle" in msg



