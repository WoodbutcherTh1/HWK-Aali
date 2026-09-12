"""Tests for the salvage finisher's pure helpers (finish_pipeline.py).

The 2026-09-12 live run imported pre-fix pipeline code (the 09-09 rewrite
dropped the tuned exam), so it would die with a NameError right after
training. finish_pipeline.py completes the missing tail. These tests pin
the guards that keep it safe: never touch a fresh verdict, never race the
old pipeline process. CPU-only, tmp files.

Run:
    .venv/Scripts/python -m pytest tests/test_finish_pipeline.py -q
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finish_pipeline as fp  # noqa: E402
import soup_pipeline as sp  # noqa: E402


def _verdict(text: str, tmp_path: Path, age_s: float = 0.0) -> Path:
    report = tmp_path / "resft_pipeline_report.md"
    report.write_text(text, encoding="utf-8")
    if age_s:
        past = time.time() - age_s
        report.touch()  # ensure mtime ordering
        import os

        os.utime(report, (past, past))
    return report


# ---------------------------------------------------------------------------
# verdict_is_fresh: idempotence guard - a real verdict means hands off
# ---------------------------------------------------------------------------

def test_fresh_promote_verdict_blocks_salvage(tmp_path: Path) -> None:
    fp.REPORT = _verdict("**Verdict: PROMOTE**", tmp_path, age_s=600)
    assert fp.verdict_is_fresh() is True


def test_fresh_nogo_verdict_blocks_salvage(tmp_path: Path) -> None:
    fp.REPORT = _verdict("**Verdict: NO-GO for promotion (tie)**", tmp_path, age_s=600)
    assert fp.verdict_is_fresh() is True


def test_incomplete_verdict_is_not_a_verdict(tmp_path: Path) -> None:
    fp.REPORT = _verdict("**Verdict: INCOMPLETE - a stage failed**", tmp_path, age_s=60)
    assert fp.verdict_is_fresh() is False


def test_stale_verdict_does_not_block(tmp_path: Path) -> None:
    fp.REPORT = _verdict("**Verdict: PROMOTE**", tmp_path, age_s=7 * 3600)
    assert fp.verdict_is_fresh() is False


def test_missing_report_does_not_block(tmp_path: Path) -> None:
    fp.REPORT = tmp_path / "missing.md"
    assert fp.verdict_is_fresh() is False


# ---------------------------------------------------------------------------
# old_pipeline_alive: the lock PID is the crashed-run pipeline - never race it
# ---------------------------------------------------------------------------

def test_old_pipeline_alive_true_for_live_pid(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "hwk_soup_pipeline.lock"
    lock.write_text(str(__import__("os").getpid()), encoding="utf-8")  # this test IS alive
    fp.LOCK_PATH = lock
    assert fp.old_pipeline_alive() is True


def test_old_pipeline_alive_false_for_dead_pid(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "hwk_soup_pipeline.lock"
    lock.write_text("999999999", encoding="utf-8")
    fp.LOCK_PATH = lock
    assert fp.old_pipeline_alive() is False


def test_old_pipeline_alive_false_for_garbage_lock(tmp_path: Path) -> None:
    lock = tmp_path / "hwk_soup_pipeline.lock"
    lock.write_text("not-a-pid", encoding="utf-8")
    fp.LOCK_PATH = lock
    assert fp.old_pipeline_alive() is False


def test_old_pipeline_alive_false_for_missing_lock(tmp_path: Path) -> None:
    fp.LOCK_PATH = tmp_path / "missing.lock"
    assert fp.old_pipeline_alive() is False


# ---------------------------------------------------------------------------
# the restored pipeline stage itself: run_exam("tuned", ...) exists in
# soup_pipeline.main (the 09-09 rewrite dropped it - NameError after hours)
# ---------------------------------------------------------------------------

def test_pipeline_main_contains_restored_tuned_exam() -> None:
    import inspect

    source = inspect.getsource(sp.main)
    assert 'tuned = run_exam("tuned"' in source, (
        "main() must grade the tuned adapter before write_verdict - the "
        "09-09 rewrite silently dropped this and crashed post-training")
    assert "write_verdict(baseline, tuned)" in source
