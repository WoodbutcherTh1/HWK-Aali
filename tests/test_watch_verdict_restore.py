"""Tests for watch_verdict_restore's verification logic (2026-09-12).

The watcher runs unattended after the graduation verdict, so its decision
function must be pinned: a PROMOTE whose promoted.json still points at the
BACKUP is a failure (fallback to restore), a NO-GO after restore is a
success only when promoted.json points at the backup. No network, no real
endpoint - models_id/endpoint_live are injected. CPU-only, tmp files.

Run:
    .venv/Scripts/python -m pytest tests/test_watch_verdict_restore.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import watch_verdict_restore as w  # noqa: E402


def _write_promoted(tmp_path: Path, adapter_dir: str) -> Path:
    path = tmp_path / "promoted.json"
    path.write_text(json.dumps({
        "adapter_dir": adapter_dir,
        "base_url": f"http://127.0.0.1:{w.PORT}/v1",
        "tuned_score": 4,
    }), encoding="utf-8")
    return path


def test_verify_new_brain_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(w, "PROMOTED_JSON",
                        _write_promoted(tmp_path, "D:/hwk-data/soup/tuned/checkpoint-3800"))
    monkeypatch.setattr(w, "endpoint_live", lambda port, timeout_s=120: True)
    monkeypatch.setattr(w, "models_id", lambda port: "checkpoint-3800")
    ok, detail = w.verify_live_brain(expect_new=True)
    assert ok
    assert "LIVE" in detail


def test_verify_new_brain_fails_when_still_backup(tmp_path: Path, monkeypatch) -> None:
    """PROMOTE path: promoted.json pointing at the backup means the promote
    never landed - must fail so the watcher falls back to the restore."""
    monkeypatch.setattr(w, "PROMOTED_JSON",
                        _write_promoted(tmp_path, str(w.BACKUP_ADAPTER)))
    monkeypatch.setattr(w, "endpoint_live", lambda port, timeout_s=120: True)
    monkeypatch.setattr(w, "models_id", lambda port: "checkpoint-2900")
    ok, detail = w.verify_live_brain(expect_new=True)
    assert not ok
    assert "BACKUP" in detail


def test_verify_restored_brain_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(w, "PROMOTED_JSON",
                        _write_promoted(tmp_path, str(w.BACKUP_ADAPTER)))
    monkeypatch.setattr(w, "endpoint_live", lambda port, timeout_s=120: True)
    monkeypatch.setattr(w, "models_id", lambda port: "checkpoint-2900")
    ok, detail = w.verify_live_brain(expect_new=False)
    assert ok


def test_verify_restored_fails_without_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(w, "PROMOTED_JSON",
                        _write_promoted(tmp_path, str(w.BACKUP_ADAPTER)))
    monkeypatch.setattr(w, "endpoint_live", lambda port, timeout_s=120: False)
    ok, detail = w.verify_live_brain(expect_new=False)
    assert not ok
    assert "no endpoint" in detail


def test_verify_fails_on_unreadable_promoted(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "promoted.json"
    bad.write_text("not json{", encoding="utf-8")
    monkeypatch.setattr(w, "PROMOTED_JSON", bad)
    monkeypatch.setattr(w, "endpoint_live", lambda port, timeout_s=120: True)
    monkeypatch.setattr(w, "models_id", lambda port: "checkpoint-3800")
    ok, detail = w.verify_live_brain(expect_new=True)
    assert not ok
    assert "unreadable" in detail


def test_verdict_text_routing() -> None:
    promote = "**Verdict: PROMOTE - the tuned model beats the baseline; adapter is at X**"
    nogo = "**Verdict: NO-GO for promotion (tie) - keep the baseline, consider more data**"
    incomplete = "**Verdict: INCOMPLETE - a stage failed; see soup_pipeline.log**"
    assert "Verdict: PROMOTE" in promote
    for other in (nogo, incomplete):
        assert "Verdict: PROMOTE" not in other
