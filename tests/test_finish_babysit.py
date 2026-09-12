"""Tests for the probe-cycle babysitter (finish_babysit.py).

The babysitter exists because the LIVE pre-fix pipeline cannot be patched:
its smoke-probe cycles starve the GPU trainer (host RAM) and a starved
probe can return empty generations - 2/2 would abort a healthy run. These
tests pin the kill-target selection: ONLY the probe python and the smoke
server on the private port - never the exam server (:20129) or the
trainer. Probe pythons die BEFORE the server so an ungraded probe can
never write an empty-report. CPU-only, no real kills.

Run:
    .venv/Scripts/python -m pytest tests/test_finish_babysit.py -q
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finish_babysit as fb  # noqa: E402


def _run_result(stdout: str):
    return type("R", (), {"stdout": stdout})()


def test_probe_pids_matches_probe_and_smoke_server_only(monkeypatch) -> None:
    entries = [
        {"ProcessId": 10,
         "CommandLine": "python scripts/soup_probe_smoke.py --port 20130"},
        {"ProcessId": 20,
         "CommandLine": "python soup.exe serve --model X --port 20130 --device cpu"},
        {"ProcessId": 30,  # the EXAM server on :20129 - must NEVER be touched
         "CommandLine": "python soup.exe serve --model Y --port 20129"},
        {"ProcessId": 40,  # the trainer - must NEVER be touched
         "CommandLine": "python soup.exe train --config soup.yaml --yes"},
    ]
    monkeypatch.setattr(fb.subprocess, "run",
                        lambda *a, **k: _run_result(json.dumps(entries)))
    assert fb.probe_pids() == ["10", "20"]


def test_probe_pids_catches_mid_load_server_without_probe_marker(monkeypatch) -> None:
    # a server still loading has no probe python marker and is not listening
    # yet - the serve+port pair is what catches it (14:55 lesson)
    entries = [{"ProcessId": 77,
                "CommandLine": "python soup.exe serve --model "
                               "D:/hwk-data/soup/tuned/checkpoint-1800 "
                               "--port 20130 --device cpu"}]
    monkeypatch.setattr(fb.subprocess, "run",
                        lambda *a, **k: _run_result(json.dumps(entries)))
    assert fb.probe_pids() == ["77"]


def test_probe_pids_tolerates_garbage_and_empty(monkeypatch) -> None:
    monkeypatch.setattr(fb.subprocess, "run",
                        lambda *a, **k: _run_result("not json"))
    assert fb.probe_pids() == []
    monkeypatch.setattr(fb.subprocess, "run",
                        lambda *a, **k: _run_result(""))
    assert fb.probe_pids() == []


def test_listening_pid_finds_the_owner(monkeypatch) -> None:
    netstat = ("  TCP    127.0.0.1:20130    0.0.0.0:0    LISTENING    4321\n"
               "  TCP    127.0.0.1:20130    1.2.3.4:55   ESTABLISHED    9999\n")
    monkeypatch.setattr(fb.subprocess, "run",
                        lambda *a, **k: _run_result(netstat))
    assert fb.listening_pid(20130) == "4321"


def test_listening_pid_none_when_port_free(monkeypatch) -> None:
    monkeypatch.setattr(fb.subprocess, "run",
                        lambda *a, **k: _run_result(""))
    assert fb.listening_pid(20130) is None


def test_old_pipeline_alive_tracks_the_lock_pid(tmp_path: Path) -> None:
    lock = tmp_path / "hwk_soup_pipeline.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")  # this test IS alive
    fb.LOCK_PATH = lock
    assert fb.old_pipeline_alive() is True
    lock.write_text("999999999", encoding="utf-8")
    assert fb.old_pipeline_alive() is False
    lock.write_text("garbage", encoding="utf-8")
    assert fb.old_pipeline_alive() is False


def test_neutralize_cycle_kills_probe_before_server(monkeypatch) -> None:
    # order matters: an alive server with a dead probe grades nothing -
    # killing the server first could let a half-finished probe write empties
    monkeypatch.setattr(fb, "probe_pids", lambda: ["11"])
    monkeypatch.setattr(fb, "listening_pid", lambda _p: "22")
    killed: list[str] = []
    monkeypatch.setattr(fb, "kill", lambda pid: killed.append(pid) or True)
    assert fb.neutralize_cycle() is True
    assert killed == ["11", "22"]


def test_neutralize_cycle_false_when_nothing_to_kill(monkeypatch) -> None:
    monkeypatch.setattr(fb, "probe_pids", lambda: [])
    monkeypatch.setattr(fb, "listening_pid", lambda _p: None)
    assert fb.neutralize_cycle() is False
