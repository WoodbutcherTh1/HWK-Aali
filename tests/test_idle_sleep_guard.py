"""Tests for scripts/idle_sleep_guard.py (PC-side sleep decision).

All tests fabricate the process table - nothing here touches the real GPU,
the real process list, or the power state. The guard's contract: exit 0 =
OK to sleep, exit 1 = WAIT, and the always-on Aali stack (brain shim +
bare python, API, watchdog) must NEVER look like a trainer.
"""
from __future__ import annotations

import pytest

import idle_sleep_guard as guard


SHIM_LINES = [
    '[113112] "C:\\Python312\\python.exe" "D:\\hwk-tools\\soup-venv\\Scripts\\soup.exe" '
    "serve --model D:\\hwk-data\\soup\\tuned\\checkpoint-3933 --port 20129~48904",
    "[48904] \"D:\\hwk-tools\\soup-venv\\Scripts\\python.exe\" "
    "\"D:\\hwk-tools\\soup-venv\\Scripts\\soup.exe\" serve --model "
    "D:\\hwk-data\\soup\\tuned\\checkpoint-3933 --port 20129~66688",
    "[66688] D:\\hwk-tools\\soup-venv\\Scripts\\soup.exe serve --model "
    "D:\\hwk-data\\soup\\tuned\\checkpoint-3933 --port 20129~50220",
    '[3208] "C:\\Python312\\python.exe" file-agent/app.py~5136',
    "[5136] .venv\\Scripts\\python.exe file-agent/app.py~2860",
    "[3764] \"C:\\Python312\\python.exe\" scripts/aali_brain_watchdog.py~152416",
    "[152416] .venv\\Scripts\\python.exe scripts/aali_brain_watchdog.py~3820",
]


def test_bare_python_child_of_serve_shim_is_expected():
    """The live-proven case: pid 113112 is a BARE python.exe (no fragment in
    its cmdline) but its ancestry carries the soup.exe serve line - the
    brain itself, never a trainer."""
    assert guard._line_for_pid(SHIM_LINES, 113112).startswith("[113112]")
    assert guard._expected_gpu_holder(SHIM_LINES, 113112) is True


def test_shim_and_direct_cmdline_holders_are_expected():
    assert guard._expected_gpu_holder(SHIM_LINES, 66688) is True   # soup.exe
    assert guard._expected_gpu_holder(SHIM_LINES, 3208) is True    # app.py real
    assert guard._expected_gpu_holder(SHIM_LINES, 3764) is True    # watchdog


def test_unknown_bare_python_is_not_expected():
    lines = SHIM_LINES + ["[99999] \"C:\\Python312\\python.exe\" -c train_thing~1"]
    assert guard._expected_gpu_holder(lines, 99999) is False


def test_missing_pid_is_not_expected():
    assert guard._expected_gpu_holder(SHIM_LINES, 424242) is False


def test_line_for_pid_tolerates_garbage():
    assert guard._line_for_pid([], 1) == ""
    assert guard._line_for_pid(["not-a-line"], 1) == ""


def _patch_all(monkeypatch, lines, gpu_pids, training_idle=999.0, sessions=""):
    monkeypatch.setattr(guard, "training_log_idle_minutes",
                        lambda: training_idle)
    monkeypatch.setattr(guard, "training_like_processes", lambda: lines)
    monkeypatch.setattr(guard, "python_compute_pids", lambda: gpu_pids)
    monkeypatch.setattr(guard, "_ps",
                        lambda cmd: sessions if cmd.startswith("Get-Process") else None)


def test_ok_with_only_the_always_on_stack(monkeypatch):
    _patch_all(monkeypatch, SHIM_LINES, [113112, 3208])
    ok, reason = guard.check_sleep_ok()
    assert ok is True
    assert "no training" in reason


def test_blocks_on_fresh_training_log(monkeypatch):
    _patch_all(monkeypatch, SHIM_LINES, [], training_idle=2.0)
    ok, reason = guard.check_sleep_ok()
    assert ok is False
    assert "training.log" in reason


def test_blocks_on_training_machinery(monkeypatch):
    lines = SHIM_LINES + ["[70000] python scripts/soup_pipeline.py~1"]
    _patch_all(monkeypatch, lines, [])
    ok, reason = guard.check_sleep_ok()
    assert ok is False
    assert "soup_pipeline" in reason


def test_blocks_on_unexpected_gpu_pid(monkeypatch):
    lines = SHIM_LINES + ["[99999] \"python.exe\" mystery~1"]
    _patch_all(monkeypatch, lines, [99999])
    ok, reason = guard.check_sleep_ok()
    assert ok is False
    assert "99999" in reason


def test_fail_closed_when_gpu_query_fails(monkeypatch):
    _patch_all(monkeypatch, SHIM_LINES, None)
    ok, reason = guard.check_sleep_ok()
    assert ok is False
    assert "cannot verify" in reason


def test_blocks_on_user_session(monkeypatch):
    _patch_all(monkeypatch, SHIM_LINES, [], sessions="chrome")
    ok, reason = guard.check_sleep_ok()
    assert ok is False
    assert "user session" in reason


def test_expected_fragments_cover_the_live_stack():
    """Contract pin: the three known servers must stay on the allow list,
    written with forward slashes (normalization handles backslashes)."""
    joined = " ".join(guard.EXPECTED_SERVER_FRAGMENTS)
    assert "file-agent/app.py" in joined
    assert "soup.exe serve" in joined
    assert "aali_brain_watchdog.py" in joined
