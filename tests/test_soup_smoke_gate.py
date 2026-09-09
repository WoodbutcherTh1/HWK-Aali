"""Tests for the soup graduation pipeline's fast-fail smoke gate and the
phantom-verdict fixes (2026-09-09: the tuned exam graded a DEAD endpoint and
produced a 0/26 'regression' that never tested the adapter).

CPU-only: every external effect (nvidia-smi, HTTP, subprocesses, live files
under D:/hwk-data) is mocked or redirected to tmp dirs. Run:
    .venv/Scripts/python -m pytest tests/test_soup_smoke_gate.py -q
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import soup_pipeline as sp  # noqa: E402
from soup_exam import SMOKE_CASE_IDS, select_cases  # noqa: E402


# ---------------------------------------------------------------------------
# select_cases (soup_exam.py): the probe runs a subset; typos must fail loudly
# ---------------------------------------------------------------------------

def _fake_cases() -> list[dict]:
    return [{"id": "img_basic_en"}, {"id": "halluc_missing_file_en"},
            {"id": "memory_save_user_directive_en"}]


def test_select_cases_filters_and_keeps_exam_order() -> None:
    # Order follows the exam file (stability over re-ordering) - the probe
    # grades the same cases in the same order as the full exam.
    cases = select_cases(_fake_cases(), ["halluc_missing_file_en", "img_basic_en"])
    assert [c["id"] for c in cases] == ["img_basic_en", "halluc_missing_file_en"]


def test_select_cases_no_ids_returns_full_exam() -> None:
    cases = _fake_cases()
    assert select_cases(cases, None) is cases
    assert select_cases(cases, []) == cases


def test_select_cases_unknown_id_exits_loudly() -> None:
    with pytest.raises(SystemExit) as excinfo:
        select_cases(_fake_cases(), ["img_basic_en", "img_basic_typo"])
    assert "img_basic_typo" in str(excinfo.value)


def test_smoke_subset_covers_six_distinct_families() -> None:
    assert len(SMOKE_CASE_IDS) == 6
    families = {i.split("_", 1)[0] for i in SMOKE_CASE_IDS}
    assert families == {"img", "halluc", "security", "memory"}


# ---------------------------------------------------------------------------
# _adapter_dir: numeric checkpoint order (lexic sort puts 10000 before 9000)
# ---------------------------------------------------------------------------

def test_adapter_dir_prefers_highest_step_numerically(tmp_path: Path, monkeypatch) -> None:
    tuned = tmp_path / "tuned"
    for step in (1442, 9000, 10000):
        (tuned / f"checkpoint-{step}").mkdir(parents=True)
        (tuned / f"checkpoint-{step}" / "adapter_model.safetensors").write_text("w")
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    assert sp._adapter_dir() == tuned / "checkpoint-10000"


def test_adapter_dir_skips_checkpoints_without_weights(tmp_path: Path, monkeypatch) -> None:
    tuned = tmp_path / "tuned"
    (tuned / "checkpoint-500").mkdir(parents=True)  # mid-save: no adapter file yet
    done = tuned / "checkpoint-300"
    done.mkdir(parents=True)
    (done / "adapter_model.safetensors").write_text("w")
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    assert sp._adapter_dir() == done


def test_adapter_dir_falls_back_to_output_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    assert sp._adapter_dir() == tmp_path / "tuned"


# ---------------------------------------------------------------------------
# _probe_passes: the smoke pass threshold
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    ("0/6", False), ("1/6", False), ("2/6", False), ("3/6", True),
    ("6/6", True), ("garbage", False),
])
def test_probe_passes_threshold(score: str, expected: bool) -> None:
    assert sp._probe_passes({"score": score}) is expected


def test_probe_passes_rejects_missing_report() -> None:
    assert sp._probe_passes(None) is False


# ---------------------------------------------------------------------------
# smoke_watcher: 2 consecutive FAILED probes abort; passes reset; a probe
# being unavailable (None) never counts as a failure
# ---------------------------------------------------------------------------

class _FakeTrainer:
    def __init__(self) -> None:
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


def _run_watcher(monkeypatch, reports: list, min_calls: int):
    calls: list[int] = []
    finished = threading.Event()

    def fake_probe(_stage: str):
        calls.append(1)
        if len(calls) >= min_calls:
            finished.set()
        return reports.pop(0) if reports else None

    monkeypatch.setattr(sp, "run_smoke_probe", fake_probe)
    monkeypatch.setattr(sp, "_cpu_serving_ok", lambda: True)
    monkeypatch.setattr(sp, "log", lambda _m: None)  # keep tests off the real log
    monkeypatch.setattr(sp, "SMOKE_POLL_S", 0.02)
    fake = _FakeTrainer()
    monkeypatch.setattr(sp, "training_process", fake)
    stop = threading.Event()
    abort_reason: list[str] = []
    thread = threading.Thread(target=sp.smoke_watcher,
                              args=(stop, abort_reason), daemon=True)
    thread.start()
    finished.wait(15)
    stop.set()
    thread.join(timeout=10)
    return fake, abort_reason, calls


def test_watcher_aborts_after_two_consecutive_failed_probes(monkeypatch) -> None:
    reports = [{"score": "1/6"}, {"score": "2/6"}]
    fake, abort_reason, calls = _run_watcher(monkeypatch, reports, min_calls=2)
    assert fake.terminated, "trainer must be terminated at the 2nd failed probe"
    assert calls == [1, 1]
    assert abort_reason and "smoke gate" in abort_reason[0]


def test_watcher_pass_resets_the_fail_counter(monkeypatch) -> None:
    # fail -> pass -> fail: the counter reset means no abort after 3 probes
    reports = [{"score": "1/6"}, {"score": "4/6"}, {"score": "2/6"}]
    fake, abort_reason, calls = _run_watcher(monkeypatch, reports, min_calls=3)
    assert not fake.terminated
    assert abort_reason == []
    assert len(calls) == 3


def test_watcher_unavailable_probe_does_not_reset_the_fail_counter(monkeypatch) -> None:
    # Only a PASS proves health: fail, unavailable, fail is still two
    # consecutive fails (an unavailable probe must not let a broken adapter
    # dodge the gate by checkpoint-timing luck), so the 2nd fail aborts.
    reports = [None, {"score": "0/6"}, None, {"score": "0/6"}]
    fake, abort_reason, calls = _run_watcher(monkeypatch, reports, min_calls=4)
    assert fake.terminated
    assert abort_reason and "smoke gate" in abort_reason[0]
    assert len(calls) == 4


def test_watcher_never_aborts_on_unavailable_probes_alone(monkeypatch) -> None:
    # None is 'cannot judge' (no checkpoint yet / CPU serve down) - it must
    # neither count as a failure nor crash the watcher.
    reports = [None, None, None]
    fake, abort_reason, calls = _run_watcher(monkeypatch, reports, min_calls=3)
    assert not fake.terminated
    assert abort_reason == []
    assert len(calls) == 3


def test_watcher_returns_without_probing_when_no_trainer(monkeypatch) -> None:
    monkeypatch.setattr(sp, "training_process", None)
    monkeypatch.setattr(sp, "log", lambda _m: None)
    stop = threading.Event()
    probe_ran = threading.Event()

    def fake_probe(_stage: str):
        probe_ran.set()
        return None

    monkeypatch.setattr(sp, "run_smoke_probe", fake_probe)
    thread = threading.Thread(target=sp.smoke_watcher, args=(stop, []), daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "watcher must return immediately without a trainer"
    assert not probe_ran.is_set()


# ---------------------------------------------------------------------------
# _wait_http: the dead-endpoint guard (phantom 0/26 lesson)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def test_wait_http_true_when_server_answers(monkeypatch) -> None:
    monkeypatch.setattr(sp.urllib.request, "urlopen",
                        lambda url, timeout: _FakeResponse(200))
    assert sp._wait_http("http://127.0.0.1:20129/v1/models", timeout_s=1) is True


def test_wait_http_false_when_endpoint_is_dead(monkeypatch) -> None:
    def _refuse(url, timeout):
        raise ConnectionError("refused")

    monkeypatch.setattr(sp.urllib.request, "urlopen", _refuse)
    monkeypatch.setattr(sp.time, "sleep", lambda _s: None)
    assert sp._wait_http("http://127.0.0.1:20129/v1/models", timeout_s=1) is False


def test_wait_http_false_on_error_status(monkeypatch) -> None:
    monkeypatch.setattr(sp.urllib.request, "urlopen",
                        lambda url, timeout: _FakeResponse(503))
    monkeypatch.setattr(sp.time, "sleep", lambda _s: None)
    assert sp._wait_http("http://127.0.0.1:20129/v1/models", timeout_s=1) is False


# ---------------------------------------------------------------------------
# write_verdict: failure reason lands in the report; PROMOTE records the gate
# ---------------------------------------------------------------------------

def test_write_verdict_includes_failure_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    baseline = {"score": "1/26"}
    text = sp.write_verdict(baseline, None, "tuned adapter server never became ready")
    assert "**Verdict: INCOMPLETE" in text
    assert "_Reason: tuned adapter server never became ready_" in text
    saved = json.loads("{}") if False else (tmp_path / "resft_pipeline_report.md").read_text(encoding="utf-8")
    assert "Reason: tuned adapter server never became ready" in saved


def test_write_verdict_promote_records_promotion(tmp_path: Path, monkeypatch) -> None:
    tuned_dir = tmp_path / "tuned" / "checkpoint-4326"
    tuned_dir.mkdir(parents=True)
    (tuned_dir / "adapter_model.safetensors").write_text("w")
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    text = sp.write_verdict({"score": "1/26"}, {"score": "5/26"})
    assert "Verdict: PROMOTE" in text
    promoted = json.loads((tmp_path / "promoted.json").read_text(encoding="utf-8"))
    assert promoted["tuned_score"] == 5
    assert promoted["baseline_score"] == 1
    assert promoted["adapter_dir"].endswith("checkpoint-4326")


def test_write_verdict_regression_keeps_baseline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    text = sp.write_verdict({"score": "1/26"}, {"score": "0/26"})
    assert "NO-GO for promotion (regression)" in text
    assert not (tmp_path / "promoted.json").exists()
