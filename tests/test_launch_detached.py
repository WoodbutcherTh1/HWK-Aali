"""Tests for the detached-launch convention (2026-09-12 12:20 lesson).

A console Ctrl+C/close event swept the soup pipeline, the trainer and the
smoke probe because they lived in a visible console. Long jobs must spawn
detached (no console, own process group) - these tests pin the launcher
utility and soup_pipeline's self-detach guard. CPU-only, no real GPU work,
no processes actually spawned.

Run:
    .venv/Scripts/python -m pytest tests/test_launch_detached.py -q
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import launch_detached as ld  # noqa: E402
import soup_pipeline as sp  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_pipeline_write_targets(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch):
    """Same debt fix as test_soup_smoke_gate.py: pipeline writes stay in tmp
    so no fake verdict line can reach the production soup_pipeline.log."""
    reports = tmp_path / "soup"
    reports.mkdir()
    monkeypatch.setattr(sp, "PIPELINE_LOG", tmp_path / "soup_pipeline.log")
    monkeypatch.setattr(sp, "LOG_TAIL", tmp_path / "soup_pipeline_last_stage.txt")
    monkeypatch.setattr(sp, "REPORTS", reports)


# ---------------------------------------------------------------------------
# launch_detached.build_command: relative exe -> absolute (against the
# caller's cwd, before the child changes directory)
# ---------------------------------------------------------------------------

def test_build_command_resolves_relative_exe(tmp_path: Path, monkeypatch) -> None:
    fake_exe = tmp_path / "venv" / "python.exe"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    command = ld.build_command(["venv/python.exe", "train_scratch.py", "--resume"])
    assert command[0] == str(fake_exe)
    assert command[1:] == ["train_scratch.py", "--resume"]


def test_build_command_keeps_absolute_exe(tmp_path: Path) -> None:
    exe = tmp_path / "tool.exe"
    command = ld.build_command([str(exe), "serve"])
    assert command == [str(exe), "serve"]


def test_build_command_rejects_empty() -> None:
    with pytest.raises(ValueError):
        ld.build_command([])


# ---------------------------------------------------------------------------
# launch_detached.spawn_detached: DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
# on Windows (no console to receive Ctrl+C/close), append-mode logs under
# D:/hwk-data, child pid returned
# ---------------------------------------------------------------------------

class _FakeChild:
    pid = 4242


def test_spawn_detached_uses_detached_flags(tmp_path: Path, monkeypatch) -> None:
    seen: dict = {}

    def fake_popen(command, **kwargs):
        seen.update(kwargs)
        seen["command"] = command
        return _FakeChild()

    monkeypatch.setattr(ld.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ld, "LOG_DIR", tmp_path)
    pid = ld.spawn_detached(["tool.exe", "run"], "unit_log", cwd=tmp_path)
    assert pid == 4242
    flags = seen["creationflags"]
    assert flags & getattr(ld.subprocess, "DETACHED_PROCESS", 0)
    assert flags & getattr(ld.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    assert seen["stdin"] == ld.subprocess.DEVNULL
    assert Path(seen["stdout"].name) == tmp_path / "unit_log.log"
    assert Path(seen["stderr"].name) == tmp_path / "unit_log.log.err"


def test_spawn_detached_appends_never_truncates(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "unit_log.log").write_text("older run\n", encoding="utf-8")
    captured: dict = {}

    def fake_popen(command, **kwargs):
        captured["out"] = kwargs["stdout"]
        return _FakeChild()

    monkeypatch.setattr(ld.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ld, "LOG_DIR", tmp_path)
    ld.spawn_detached(["tool.exe"], "unit_log", cwd=tmp_path)
    assert captured["out"].mode.startswith("a"), "logs must append across runs"


# ---------------------------------------------------------------------------
# soup_pipeline.self_detach: first entry re-spawns itself with the env
# marker and exits; second entry (marker set) runs in-place
# ---------------------------------------------------------------------------

def test_self_detach_respawns_once_with_marker(monkeypatch, tmp_path: Path) -> None:
    spawned: dict = {}
    marker = sp.DETACH_ENV

    def fake_popen(argv, **kwargs):
        spawned["argv"] = argv
        spawned["env"] = kwargs["env"]
        spawned["creationflags"] = kwargs.get("creationflags", 0)
        return _FakeChild()

    monkeypatch.setattr(sp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    monkeypatch.delenv(marker, raising=False)
    # first entry: returns False -> caller exits without running the pipeline
    assert sp.self_detach(no_wait=True) is False
    assert spawned["argv"][-1] == "--no-wait", "caller's --no-wait must pass through"
    assert spawned["env"][marker] == "1"
    flags = spawned["creationflags"]
    assert flags & getattr(sp.subprocess, "DETACHED_PROCESS", 0)
    assert flags & getattr(sp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def test_self_detach_runs_inplace_when_marker_set(monkeypatch, tmp_path: Path) -> None:
    def _explode(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("re-spawned despite the marker being set")

    monkeypatch.setattr(sp.subprocess, "Popen", _explode)
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    monkeypatch.setenv(sp.DETACH_ENV, "1")
    assert sp.self_detach() is True


def test_self_detach_runs_inplace_without_psutil(monkeypatch, tmp_path: Path) -> None:
    def _explode(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("re-spawned without psutil")

    monkeypatch.setattr(sp.subprocess, "Popen", _explode)
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    monkeypatch.setattr(sp, "psutil", None)
    monkeypatch.delenv(sp.DETACH_ENV, raising=False)
    assert sp.self_detach() is True, "no psutil = cannot re-spawn = run in-place"


# ---------------------------------------------------------------------------
# Job-object breakaway (2026-09-12 16:29 lesson, attempt 8): a scheduled
# task runs its python inside a JOB OBJECT with kill-on-close; a detached
# child that INHERITS the job dies with the parent's exit. The child must
# ask to BREAK AWAY at birth; where the job refuses, fall back to the
# plain detached flags so the launch still works.
# ---------------------------------------------------------------------------

_BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def test_detached_creationflags_include_breakaway() -> None:
    flags = ld.detached_creationflags()
    if sys.platform != "win32":
        assert flags == 0
        return
    assert flags & _DETACHED and flags & _NEW_GROUP and flags & _BREAKAWAY


def test_spawn_detached_asks_for_breakaway(monkeypatch, tmp_path: Path) -> None:
    seen: dict = {}

    def fake_popen(command, **kwargs):
        seen["flags"] = kwargs["creationflags"]
        return _FakeChild()

    monkeypatch.setattr(ld.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ld, "LOG_DIR", tmp_path)
    ld.spawn_detached(["tool.exe"], "unit_log", cwd=tmp_path)
    if sys.platform == "win32":
        assert seen["flags"] & _BREAKAWAY, "child must leave the parent's job"


def test_spawn_detached_falls_back_when_breakaway_refused(
        monkeypatch, tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_popen(command, **kwargs):
        calls.append(kwargs["creationflags"])
        if kwargs["creationflags"] & _BREAKAWAY:
            raise OSError("access denied: job does not allow breakaway")
        return _FakeChild()

    monkeypatch.setattr(ld.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ld, "LOG_DIR", tmp_path)
    pid = ld.spawn_detached(["tool.exe"], "unit_log", cwd=tmp_path)
    assert pid == 4242
    if sys.platform == "win32":
        assert len(calls) == 2, "one breakaway attempt + one fallback"
        assert calls[0] & _BREAKAWAY
        assert not (calls[1] & _BREAKAWAY), "fallback must drop the breakaway"
        assert calls[1] & _DETACHED and calls[1] & _NEW_GROUP
    else:
        assert len(calls) == 1


def test_self_detach_respawn_breaks_away_from_job(monkeypatch, tmp_path: Path) -> None:
    spawned: dict = {}

    def fake_popen(argv, **kwargs):
        spawned["flags"] = kwargs.get("creationflags", 0)
        return _FakeChild()

    monkeypatch.setattr(sp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    monkeypatch.delenv(sp.DETACH_ENV, raising=False)
    assert sp.self_detach() is False
    if sys.platform == "win32":
        assert spawned["flags"] & _BREAKAWAY, "pipeline child must break away"


def test_self_detach_falls_back_when_breakaway_refused(
        monkeypatch, tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_popen(argv, **kwargs):
        calls.append(kwargs.get("creationflags", 0))
        if kwargs.get("creationflags", 0) & _BREAKAWAY:
            raise OSError("breakaway refused")
        return _FakeChild()

    monkeypatch.setattr(sp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sp, "REPORTS", tmp_path)
    monkeypatch.delenv(sp.DETACH_ENV, raising=False)
    assert sp.self_detach() is False, "fallback still spawns the child"
    if sys.platform == "win32":
        assert len(calls) == 2
        assert calls[0] & _BREAKAWAY
        assert not (calls[1] & _BREAKAWAY)
