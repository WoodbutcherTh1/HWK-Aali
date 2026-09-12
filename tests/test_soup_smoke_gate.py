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
# _probe_passes: behavioral threshold (now TELEMETRY ONLY at mid-train)
# _probe_is_broken: the abort condition - empty generations = dead endpoint
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    ("0/6", False), ("1/6", False), ("2/6", False), ("3/6", True),
    ("6/6", True), ("garbage", False),
])
def test_probe_passes_threshold(score: str, expected: bool) -> None:
    assert sp._probe_passes({"score": score}) is expected


def test_probe_passes_rejects_missing_report() -> None:
    assert sp._probe_passes(None) is False


def _graded_report(raw_outputs: list[str]) -> dict:
    return {"score": f"{sum(1 for r in raw_outputs if r.strip())}/6",
            "results": [{"id": f"case{i}", "raw_output": r}
                        for i, r in enumerate(raw_outputs)]}


def test_probe_broken_when_all_generations_empty() -> None:
    # the phantom-verdict signature (2026-09-09): 26 empty replies were
    # graded a 0/26 'regression' - THIS is what must abort training
    assert sp._probe_is_broken(_graded_report(["", "", "", "", "", ""]))


def test_probe_broken_when_only_one_case_answers() -> None:
    assert sp._probe_is_broken(_graded_report(["hi", "", "", "", "", ""]))


def test_probe_not_broken_on_low_but_real_outputs() -> None:
    # the 2026-09-12 calibration: a healthy 1.5B student mid-training scores
    # 0/6 with REAL text (protocol learned, behaviors not yet) - never abort
    report = _graded_report([
        "I'm sorry, but as an AI language model, I don't have the capability",
        '{"tool":"file","arguments":{"path":"/x"}}',
        "لا يمكنني الوصول إلى الملف",
        '{"tool":"run","arguments":{"command":"ls"}}',
        '{"tool":"final","content":"ok"}',
        "",
    ])
    report["score"] = "0/6"  # real text, zero BEHAVIORAL passes (helper counts raws)
    assert not sp._probe_is_broken(report)
    assert not sp._probe_passes(report)  # telemetry says: keep training


def test_probe_not_broken_on_full_pass() -> None:
    assert not sp._probe_is_broken(_graded_report(["a", "b", "c", "d", "e", "f"]))


def test_probe_broken_on_untrustworthy_report_without_results() -> None:
    # a graded report with a score but NO visible outputs cannot be trusted
    assert sp._probe_is_broken({"score": "0/6"}) is True
    # an EMPTY report is indistinguishable from 'no data yet' -> unavailable
    assert sp._probe_is_broken({}) is False
    assert sp._probe_is_broken(None) is False  # unavailable: caller decides


# ---------------------------------------------------------------------------
# smoke_watcher: 2 consecutive BROKEN probes (empty generations) abort;
# low-but-real scores are telemetry and never abort; a probe being
# unavailable (None) never counts as broken
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


def test_watcher_aborts_after_two_consecutive_broken_probes(monkeypatch) -> None:
    empty = _graded_report(["", "", "", "", "", ""])
    reports = [empty, empty]
    fake, abort_reason, calls = _run_watcher(monkeypatch, reports, min_calls=2)
    assert fake.terminated, "trainer must be terminated at the 2nd broken probe"
    assert calls == [1, 1]
    assert abort_reason and "smoke gate" in abort_reason[0]


def test_watcher_low_scores_are_telemetry_and_never_abort(monkeypatch) -> None:
    # the 2026-09-12 regression: healthy run killed at 30% by 0/6-with-text
    low = _graded_report(["real text", "more text", "x", "y", "z", "w"])
    reports = [low, low, low, low]
    fake, abort_reason, calls = _run_watcher(monkeypatch, reports, min_calls=4)
    assert not fake.terminated, "real-but-low scores must never abort"
    assert abort_reason == []
    assert len(calls) == 4


def test_watcher_broken_counter_resets_on_alive_probe(monkeypatch) -> None:
    empty = _graded_report(["", "", "", "", "", ""])
    alive = _graded_report(["a", "b", "c", "d", "e", ""])
    reports = [empty, alive, empty, alive]  # broken, alive, broken, alive
    fake, abort_reason, calls = _run_watcher(monkeypatch, reports, min_calls=4)
    assert not fake.terminated
    assert abort_reason == []
    assert len(calls) == 4


def test_watcher_unavailable_probe_does_not_reset_the_broken_counter(monkeypatch) -> None:
    # Only an ALIVE probe proves health: broken, unavailable, broken still
    # aborts (unavailable must not let a dead endpoint dodge the gate).
    empty = _graded_report(["", "", "", "", "", ""])
    reports = [None, empty, None, empty]
    fake, abort_reason, calls = _run_watcher(monkeypatch, reports, min_calls=4)
    assert fake.terminated
    assert abort_reason and "smoke gate" in abort_reason[0]
    assert len(calls) == 4


def test_watcher_never_aborts_on_unavailable_probes_alone(monkeypatch) -> None:
    # None is 'cannot judge' (no checkpoint yet / CPU serve down) - it must
    # neither count as broken nor crash the watcher.
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
    monkeypatch.setattr(sp, "log", lambda _m: None)  # keep the real log clean
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
    monkeypatch.setattr(sp, "log", lambda _m: None)  # keep the real log clean
    text = sp.write_verdict({"score": "1/26"}, {"score": "5/26"})
    assert "Verdict: PROMOTE" in text
    promoted = json.loads((tmp_path / "promoted.json").read_text(encoding="utf-8"))
    assert promoted["tuned_score"] == 5
    assert promoted["baseline_score"] == 1
    assert promoted["adapter_dir"].endswith("checkpoint-4326")


def test_write_verdict_regression_keeps_baseline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    monkeypatch.setattr(sp, "log", lambda _m: None)  # keep the real log clean
    text = sp.write_verdict({"score": "1/26"}, {"score": "0/26"})
    assert "NO-GO for promotion (regression)" in text
    assert not (tmp_path / "promoted.json").exists()


# ---------------------------------------------------------------------------
# RAM starvation fix (2026-09-12 morning): with ~0.3 GB free (the GPU trainer
# holding the rest) the CPU serve thrashed into the pagefile and never became
# ready - three exit-3 cycles with no clue. The probe now pre-flights free RAM
# (exit 4 = skip, never a failure) and captures the server's output so a
# late/dying server shows WHY instead of a bare timeout.
# ---------------------------------------------------------------------------

import soup_probe_smoke as sprobe  # noqa: E402


def _probe_argv(tmp_path: Path) -> None:
    sys.argv = ["soup_probe_smoke.py", "--model", "D:/nonexistent/checkpoint",
                "--port", "20130", "--output", str(tmp_path / "smoke.json")]


class _DeadServer:
    def terminate(self) -> None:
        pass

    def wait(self, timeout: int = 0) -> int:
        return 0

    def kill(self) -> None:
        pass


def test_free_ram_gb_returns_number_or_none() -> None:
    # must never raise: a measurement failure means None (fail-open)
    value = sprobe._free_ram_gb()
    assert value is None or value > 0


def test_probe_skips_fast_when_ram_starved(tmp_path: Path, monkeypatch,
                                           capsys) -> None:
    _probe_argv(tmp_path)
    launched = []

    def _no_launch(*_a, **_k):  # the server must never be attempted
        launched.append(1)
        raise AssertionError("server started despite RAM gate")

    monkeypatch.setattr(sprobe, "_free_ram_gb", lambda: 0.3)
    monkeypatch.setattr(sprobe.subprocess, "Popen", _no_launch)
    assert sprobe.main() == 4
    assert not launched
    out = capsys.readouterr().out
    assert "0.30 GB RAM free" in out and "skipped" in out


def test_probe_does_not_gate_when_ram_unknown(tmp_path: Path, monkeypatch) -> None:
    # None = cannot measure -> fail-open (the gate must never disable the
    # probe on machines where RAM cannot be read)
    _probe_argv(tmp_path)
    monkeypatch.setattr(sprobe, "_free_ram_gb", lambda: None)
    monkeypatch.setattr(sprobe, "wait_http", lambda _u, timeout_s: False)
    monkeypatch.setattr(sprobe.subprocess, "Popen", lambda *a, **k: _DeadServer())
    # server never ready -> the OLD exit 3 path, still intact
    assert sprobe.main() == 3


def test_probe_exit3_dumps_server_tail_for_diagnosis(tmp_path: Path, monkeypatch,
                                                     capsys) -> None:
    _probe_argv(tmp_path)
    monkeypatch.setattr(sprobe, "_free_ram_gb", lambda: 8.0)
    monkeypatch.setattr(sprobe, "wait_http", lambda _u, timeout_s: False)
    monkeypatch.setattr(sprobe.subprocess, "Popen", lambda *a, **k: _DeadServer())
    monkeypatch.setattr(sprobe, "SERVER_LOG", tmp_path / "server.log")
    (tmp_path / "server.log").write_text("CUDA out of memory on CPU? no - RAM", encoding="utf-8")
    assert sprobe.main() == 3
    out = capsys.readouterr().out
    assert "never became ready" in out
    assert "--- soup serve (cpu) tail ---" in out
    assert "RAM" in out  # the server's own words are now visible


def _capture_pipeline_log(monkeypatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(sp, "log", lines.append)
    return lines


def test_pipeline_maps_probe_exit4_to_skip(tmp_path: Path, monkeypatch) -> None:
    # exit 4 (RAM-starved) is 'unavailable', never a failed probe: it must
    # not log FAILED and must not count toward the watcher's broken counter
    tuned = tmp_path / "tuned" / "checkpoint-900"
    tuned.mkdir(parents=True)
    (tuned / "adapter_model.safetensors").write_text("w")
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    monkeypatch.setattr(sp, "SMOKE_LOG_TAIL", tmp_path / "tail.txt")
    log_lines = _capture_pipeline_log(monkeypatch)

    class _Result:
        returncode = 4
        stdout = "probe skipped: only 0.30 GB RAM free"
        stderr = ""

    monkeypatch.setattr(sp.subprocess, "run", lambda *a, **k: _Result())
    assert sp.run_smoke_probe("midtrain") is None
    assert any("skipped" in line and "RAM" in line for line in log_lines)
    assert not any("FAILED" in line for line in log_lines)


def test_pipeline_keeps_exit3_as_failure(tmp_path: Path, monkeypatch) -> None:
    # a real exit 3 (server started, never became ready) still logs FAILED
    tuned = tmp_path / "tuned" / "checkpoint-900"
    tuned.mkdir(parents=True)
    (tuned / "adapter_model.safetensors").write_text("w")
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    monkeypatch.setattr(sp, "SMOKE_LOG_TAIL", tmp_path / "tail.txt")
    log_lines = _capture_pipeline_log(monkeypatch)

    class _Result:
        returncode = 3
        stdout = "probe server never became ready on :20130"
        stderr = ""

    monkeypatch.setattr(sp.subprocess, "run", lambda *a, **k: _Result())
    assert sp.run_smoke_probe("midtrain") is None
    assert any("FAILED (exit 3)" in line for line in log_lines)
