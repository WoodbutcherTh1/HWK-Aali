"""Server watchdog (2026-09-22): revive Aali mid-session via the autostart guard.

Pins the contracts: health = HTTP 200 AND ok:true, single instance via a
pidlock whose stale entries are taken over, revival = fire aali_autostart.bat
(never kill anything), and run_cycle's consecutive-down counter with
state-change-only logging.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "aali_server_watchdog", str(ROOT / "scripts" / "aali_server_watchdog.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def wd(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    monkeypatch.setattr(mod, "PIDLOCK", tmp_path / "aali_server_watchdog.pidlock")
    monkeypatch.setattr(mod, "GUARD_BAT", tmp_path / "aali_autostart.bat")
    return mod


@contextmanager
def _capture_log(wd):
    out = []
    with mock.patch.object(wd, "log", side_effect=out.append):
        yield out


def _fake_response(payload: bytes):
    """urlopen is used as a context manager -> wire __enter__ to self."""
    resp = mock.MagicMock()
    resp.read.return_value = payload
    resp.__enter__.return_value = resp
    return resp


# ------------------------------------------------------------------ health

def test_health_true_on_ok_payload(wd):
    with mock.patch.object(wd.urllib.request, "urlopen",
                           return_value=_fake_response(b'{"ok": true}')):
        assert wd.check_health() is True


def test_health_false_on_error_or_bad_payload(wd):
    with mock.patch.object(wd.urllib.request, "urlopen",
                           side_effect=OSError("refused")):
        assert wd.check_health() is False
    with mock.patch.object(wd.urllib.request, "urlopen",
                           return_value=_fake_response(b'{"ok": false}')):
        assert wd.check_health() is False  # HTTP 200 but not ok -> down
    with mock.patch.object(wd.urllib.request, "urlopen",
                           return_value=_fake_response(b"<html>not json</html>")):
        assert wd.check_health() is False


# ------------------------------------------------------- single instance

def test_single_instance_first_and_second(wd):
    assert wd.acquire_single_instance() is True
    wd.os.getpid = mock.MagicMock(return_value=wd.os.getpid() + 1)
    with mock.patch.object(wd, "pid_running", return_value=True):
        assert wd.acquire_single_instance() is False  # live owner holds it


def test_stale_pidlock_is_taken_over(wd):
    wd.PIDLOCK.write_text("999999", encoding="ascii")
    with mock.patch.object(wd, "pid_running", return_value=False):
        assert wd.acquire_single_instance() is True
    assert wd.PIDLOCK.read_text(encoding="ascii") == str(os.getpid())


def test_pid_running_true_on_tasklist_hit(wd):
    fake = mock.MagicMock(returncode=0, stdout="python.exe    12345 Console ...")
    with mock.patch.object(wd.subprocess, "run", return_value=fake):
        assert wd.pid_running(12345) is True
    fake.stdout = "INFO: No tasks are running..."
    with mock.patch.object(wd.subprocess, "run", return_value=fake):
        assert wd.pid_running(12345) is False


# ---------------------------------------------------------------- revival

def test_fire_guard_runs_cmd(wd):
    fake = mock.MagicMock(returncode=0)
    with mock.patch.object(wd.subprocess, "run", return_value=fake) as run:
        assert wd.fire_guard() is True
    cmd = run.call_args[0][0]
    assert cmd[0] == "cmd" and cmd[1] == "/c"
    assert cmd[2].endswith("aali_autostart.bat")


def test_fire_guard_false_on_failure(wd):
    with mock.patch.object(wd.subprocess, "run", side_effect=OSError("boom")):
        assert wd.fire_guard() is False


def test_cycle_healthy_no_guard(wd, wd_fixture=None):
    mod = _load()
    with mock.patch.object(mod, "check_health", return_value=True), \
         mock.patch.object(mod, "fire_guard") as guard, \
         _capture_log(mod) as out:
        counter = [0]
        assert mod.run_cycle(url="x", guard_bat=Path("g"),
                             settle_seconds=0, down_fails=counter) is True
        guard.assert_not_called()
        assert counter == [0]
        assert out == []          # steady state logs NOTHING (no spam)


def test_cycle_down_fires_guard_and_recovers(wd=None):
    mod = _load()
    with mock.patch.object(mod, "check_health", side_effect=[False, True]), \
         mock.patch.object(mod, "fire_guard", return_value=True) as guard, \
         mock.patch.object(mod.time, "sleep") as slept, \
         _capture_log(mod) as out:
        counter = [0]
        ok = mod.run_cycle(url="x", guard_bat=Path("g"),
                           settle_seconds=7, down_fails=counter)
        assert ok is True
        guard.assert_called_once()
        slept.assert_called_once_with(7)
        assert counter == [0]   # revived in-cycle -> counter reset to zero
        assert any("DOWN" in line for line in out)
        assert any("revived" in line for line in out)


def test_cycle_still_down_after_guard(wd=None):
    mod = _load()
    with mock.patch.object(mod, "check_health", return_value=False), \
         mock.patch.object(mod, "fire_guard", return_value=True), \
         mock.patch.object(mod.time, "sleep"), \
         _capture_log(mod):
        counter = [2]  # already was down twice before this cycle
        ok = mod.run_cycle(url="x", guard_bat=Path("g"),
                           settle_seconds=0, down_fails=counter)
        assert ok is False
        assert counter == [3]  # consecutive-down counter kept climbing


# ------------------------------------------------------------------ main

def test_main_once_zero_cycles(wd, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["aali_server_watchdog.py", "--once",
                         "--settle-seconds", "0"])
    with mock.patch.object(wd, "run_cycle", return_value=True) as cycle, \
         mock.patch.object(wd, "acquire_single_instance", return_value=True):
        assert wd.main() == 0
    assert cycle.call_count == 1


def test_main_exits_clean_when_already_running(wd, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aali_server_watchdog.py", "--once"])
    with mock.patch.object(wd, "acquire_single_instance", return_value=False):
        assert wd.main() == 0
