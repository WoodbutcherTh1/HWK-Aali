"""Exam-integrity defense (2026-10-01, the v7 26-vs-39 lesson).

The deployed soup exam (D:/hwk-data/soup/exam_prompts.jsonl) was silently
reset to the repo's smaller file mid-graduation: the night caretaker's
rebuild step ran scripts/soup_export_sft.py while the v7 pipeline was
serving its teacher, and the run graded 26 cases instead of 39. Two fixes
pinned here:

1. soup_pipeline.ensure_exam_integrity() re-syncs the deployed exam from
   the repo exam (data/exam_tool_calling.jsonl) before every grading stage;
2. the night caretaker never runs its rebuild/export while a soup pipeline
   is live.

Also pins the restored exam itself: 39 cases, the 13 toolbelt cases back
in, every SMOKE_CASE_IDS present.

CPU-only; every write target is redirected into tmp.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import soup_pipeline as sp  # noqa: E402
from soup_exam import SMOKE_CASE_IDS  # noqa: E402

NEW_TOOLBELT_IDS = (
    "spawn_call_en", "spawn_call_ar", "spawn_dependent_refusal_en",
    "excel_write_ar", "excel_read_en", "plot_call_en",
    "plot_invented_refusal_en", "diff_ar", "todo_en", "recall_en",
    "artifact_en", "screenshot_explicit_en", "screenshot_proactive_refusal_en",
)


@pytest.fixture(autouse=True)
def _isolate_pipeline_write_targets(tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch):
    """Same contract as test_soup_smoke_gate: nothing touches D:/hwk-data."""
    soup_dir = tmp_path / "soup"
    soup_dir.mkdir()
    monkeypatch.setattr(sp, "PIPELINE_LOG", tmp_path / "soup_pipeline.log")
    monkeypatch.setattr(sp, "LOG_TAIL", tmp_path / "soup_pipeline_last_stage.txt")
    monkeypatch.setattr(sp, "REPORTS", soup_dir)
    monkeypatch.setattr(sp, "EXAM_PROMPTS", soup_dir / "exam_prompts.jsonl")


def _write_repo_exam(path: Path, cases: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
        encoding="utf-8",
    )


def _two_case_repo(tmp_path: Path) -> tuple[Path, list[dict]]:
    repo = tmp_path / "repo_exam.jsonl"
    system = ("You are Aali. For tools reply {\"tool\": name, \"arguments\": {…}}. "
              "For the final answer use {\"tool\": \"final\", \"content\": reply}.")
    cases = [
        {"id": "case_a", "system": system,
         "turns": [["user", "Draw a small red boat."]],
         "expect_tool": "generate_image", "required_args": ["prompt", "path"]},
        {"id": "case_b", "system": system,
         "turns": [["user", "What is 2+2?"]], "expect_tool": "final"},
    ]
    _write_repo_exam(repo, cases)
    return repo, cases


def test_repo_exam_has_the_restored_39_cases() -> None:
    """Regression pin for the restoration: the toolbelt cases are BACK in the
    committed exam and must never silently shrink again (v7 graded 26)."""
    cases = [
        json.loads(line)
        for line in (ROOT / "data" / "exam_tool_calling.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    ids = {c["id"] for c in cases}
    assert len(cases) == 39
    missing = [i for i in NEW_TOOLBELT_IDS if i not in ids]
    assert missing == [], f"toolbelt exam cases missing again: {missing}"
    assert all(i in ids for i in SMOKE_CASE_IDS), "smoke probe ids missing"


def test_sync_restores_clobbered_deployed_exam(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployed exam reset to a smaller/older file is re-rendered from the
    repo exam before grading (the exact v7 failure shape)."""
    repo, cases = _two_case_repo(tmp_path)
    monkeypatch.setattr(sp, "REPO_EXAM", repo)
    sp.EXAM_PROMPTS.write_text(
        json.dumps({"id": "stale_case", "prompt": "System: old\nUser: old\nAssistant:"},
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert sp.ensure_exam_integrity() is True
    deployed = [
        json.loads(line)
        for line in sp.EXAM_PROMPTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    from soup_export_sft import _exam_prompt
    assert [d["id"] for d in deployed] == [c["id"] for c in cases]
    for d, c in zip(deployed, cases):
        assert d["prompt"] == _exam_prompt(c)[0]
        assert d["expect_tool"] == c.get("expect_tool")
        assert d["required_args"] == c.get("required_args", [])


def test_sync_leaves_matching_deployed_exam_untouched(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _cases = _two_case_repo(tmp_path)
    monkeypatch.setattr(sp, "REPO_EXAM", repo)
    # Render the deployed file through the SAME code path the exporter uses,
    # then confirm the sync is a no-op (byte-identical, idempotent).
    from soup_export_sft import _exam_prompt
    rows = []
    for case in _cases:
        prompt, case_id = _exam_prompt(case)
        rows.append({"id": case_id, "prompt": prompt,
                     "expect_tool": case.get("expect_tool"),
                     "required_args": case.get("required_args", []),
                     "require_disclaimer": case.get("require_disclaimer", False),
                     "disclaimer_keywords": case.get("disclaimer_keywords", [])})
    before = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    sp.EXAM_PROMPTS.write_text(before, encoding="utf-8")
    assert sp.ensure_exam_integrity() is True
    assert sp.EXAM_PROMPTS.read_text(encoding="utf-8") == before


def test_sync_creates_missing_deployed_exam(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, cases = _two_case_repo(tmp_path)
    monkeypatch.setattr(sp, "REPO_EXAM", repo)
    assert not sp.EXAM_PROMPTS.exists()
    assert sp.ensure_exam_integrity() is True
    deployed = [
        json.loads(line)
        for line in sp.EXAM_PROMPTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [d["id"] for d in deployed] == [c["id"] for c in cases]


def test_sync_survives_unreadable_repo_exam(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken repo exam must not destroy whatever is deployed - fail open
    with a loud log, never write garbage."""
    repo = tmp_path / "broken.jsonl"
    repo.write_text("{not json\n", encoding="utf-8")
    monkeypatch.setattr(sp, "REPO_EXAM", repo)
    keep = json.dumps({"id": "keep", "prompt": "p"}, ensure_ascii=False) + "\n"
    sp.EXAM_PROMPTS.write_text(keep, encoding="utf-8")
    assert sp.ensure_exam_integrity() is False
    assert sp.EXAM_PROMPTS.read_text(encoding="utf-8") == keep


def test_run_exam_syncs_before_grading() -> None:
    """Source tripwire: every run_exam call re-syncs the exam first - a
    future refactor that drops the call must fail this test."""
    import inspect
    source = inspect.getsource(sp.run_exam)
    head = source[:700]
    assert "ensure_exam_integrity()" in head, \
        "run_exam must re-sync the exam from the repo before spawning soup_exam"


def test_pipeline_start_syncs_exam() -> None:
    import inspect
    source = inspect.getsource(sp.main)
    start_index = source.find('"=== soup pipeline start ==="')
    assert start_index != -1
    sync_index = source.find("ensure_exam_integrity()", start_index)
    assert sync_index != -1, "main() must sync the exam right after start"


def test_caretaker_never_rebuilds_while_pipeline_runs() -> None:
    """The v7 root cause, pinned at the source: the caretaker's rebuild/
    export block is gated on process_running("soup_pipeline")."""
    source = (ROOT / "scripts" / "night_caretaker.py").read_text(encoding="utf-8")
    guard = source.find('process_running("soup_pipeline") is not None')
    rebuild = source.find("build_aali_sft_v2.py")
    export = source.find("soup_export_sft.py")
    assert guard != -1, "caretaker lost its pipeline-alive guard"
    assert 0 < guard < rebuild < export, \
        "the guard must precede the rebuild/export invocations"
