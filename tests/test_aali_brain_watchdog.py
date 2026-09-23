"""Tests for the 2026-09-23 brain watchdog (scripts/aali_brain_watchdog.py).

The :20129 brain had NO watchdog while the :5055 API had one - the soup
server died after the trainer took the card and nothing revived it (the
owner hit "[خطأ] تعذر الاتصال بمزود الذكاء" while /api/health said ok).

Contract pinned here (all injected - no network, no GPU, no real spawns):
- brain up          -> probe passes, NOTHING is spawned (never touches a
                       healthy brain);
- brain down, no
  promoted adapter  -> honest no-revival, no crash, logged once;
- brain down, GPU
  busy              -> revival WAITS (never fights the trainer) and the
                       wait-reason is logged once, not spammed;
- brain down, GPU
  free              -> spawn serve, wait, verify, count as revived;
- serve process
  dies early        -> honest failure, no false "revived";
- wrong model id    -> warning + left up (human decision), no kill;
- repeated DOWNs    -> revive only when the card frees (trailing retry);
- kill switch, pidlock single-instance.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

bw = importlib.import_module("aali_brain_watchdog")


@pytest.fixture(autouse=True)
def _tmp_paths(tmp_path, monkeypatch):
    """Never touch the real D:/hwk-data logs or pidlock in tests."""
    monkeypatch.setattr(bw, "LOGFILE", tmp_path / "brain_watchdog.log")
    monkeypatch.setattr(bw, "SERVE_LOG", tmp_path / "brain_serve.log")
    monkeypatch.setattr(bw, "PIDLOCK", tmp_path / "brain_watchdog.pidlock")
    monkeypatch.setattr(bw, "PROMOTED_JSON", tmp_path / "promoted.json")
    # Log calls write via the module-level LOGFILE indirection; keep prints
    # visible in failure output but harmless.


@pytest.fixture
def adapter(tmp_path):
    d = tmp_path / "tuned" / "checkpoint-3873"
    d.mkdir(parents=True)
    (d / "adapter_model.safetensors").write_bytes(b"stub")
    (tmp_path / "promoted.json").write_text(
        '{"adapter_dir": "%s"}' % d.as_posix(), encoding="utf-8")
    return d


def _probe(up=True, model_id="checkpoint-3873"):
    def fn(port=20129, timeout=4):
        return (up, model_id if up else None)
    return fn


def _safe(ok=True, reason="1 python-family compute process (train_scratch)"):
    def fn(threshold_mib=1500):
        return (ok, reason)
    return fn


def _spawn(record, die=False):
    class Proc:
        def __init__(self):
            self._dead = die

        def poll(self):
            return 1 if self._dead else None

    def fn(adapter):
        record.append(adapter)
        return Proc()
    return fn


# ---------------------------------------------------------------- up cases

def test_healthy_brain_is_never_touched(adapter):
    recorded = []
    down = [0]
    ok = bw.run_cycle(probe=_probe(True), safe=_safe(False), quiet={},
                      down_fails=down,
                      spawn=lambda a: recorded.append(a) or (_ for _ in ()).throw(
                          AssertionError("must not spawn")))
    assert ok is True
    assert recorded == []
    assert down == [0]


def test_quiet_when_healthy_across_cycles(adapter):
    down = [1]                       # came back after a down period
    bw.run_cycle(probe=_probe(True), safe=_safe(True), down_fails=down,
                 spawn=_spawn([]), quiet={})
    assert down == [0]


# ------------------------------------------------------------ no-adapter

def test_down_without_promoted_adapter_is_honest(adapter, tmp_path):
    (tmp_path / "promoted.json").write_text("{}", encoding="utf-8")
    quiet, down = {}, [0]
    ok = bw.run_cycle(probe=_probe(False), safe=_safe(True),
                      promoted=lambda: None, down_fails=down,
                      spawn=_spawn([]), quiet=quiet)
    assert ok is False
    assert quiet == {"no_adapter": True}   # logged once, honestly
    assert down == [1]


def test_missing_adapter_dir_is_not_revivable(adapter, tmp_path):
    (tmp_path / "promoted.json").write_text(
        '{"adapter_dir": "D:/hwk-data/soup/does-not-exist"}',
        encoding="utf-8")
    quiet, down = {}, [0]
    ok = bw.run_cycle(probe=_probe(False), safe=_safe(True),
                      promoted=bw.promoted_adapter, down_fails=down,
                      spawn=_spawn([]), quiet=quiet)
    assert ok is False


# ------------------------------------------------------------ GPU-busy

def test_busy_card_waits_and_never_spawns(adapter):
    recorded = []
    quiet, down = {}, [0]
    ok = bw.run_cycle(probe=_probe(False), safe=_safe(False),
                      down_fails=down, spawn=_spawn(recorded), quiet=quiet)
    assert ok is False
    assert recorded == []
    assert down == [1]           # still down, honestly counted


def test_wait_reason_logged_once_not_spammed(adapter):
    quiet, down = {}, [0]
    for _ in range(4):
        bw.run_cycle(probe=_probe(False), safe=_safe(False, "trainer holds card"),
                     down_fails=down, spawn=_spawn([]), quiet=quiet)
    # quiet dict holds the last reason; nothing re-spawned, still honest
    assert quiet.get("wait") == "trainer holds card"
    assert down == [4]


def test_wait_reason_change_relogs(adapter):
    quiet, down = {}, [0]
    bw.run_cycle(probe=_probe(False), safe=_safe(False, "reason A"),
                 down_fails=down, spawn=_spawn([]), quiet=quiet)
    bw.run_cycle(probe=_probe(False), safe=_safe(False, "reason A"),
                 down_fails=down, spawn=_spawn([]), quiet=quiet)
    bw.run_cycle(probe=_probe(False), safe=_safe(False, "reason B"),
                 down_fails=down, spawn=_spawn([]), quiet=quiet)
    assert quiet.get("wait") == "reason B"


# ---------------------------------------------------------- revival path

def test_revival_spawns_and_verifies(adapter):
    quiet, down = {}, [0]
    ok = bw.run_cycle(probe=_probe(False), safe=_safe(True),
                      down_fails=down, spawn=_spawn([]),
                      ready=lambda proc: (True, "checkpoint-3873"),
                      quiet=quiet)
    assert ok is True
    assert down == [0]


def test_serve_process_dying_is_not_revived(adapter):
    quiet, down = {}, [0]
    ok = bw.run_cycle(probe=_probe(False), safe=_safe(True),
                      down_fails=down, spawn=_spawn([], die=True),
                      ready=lambda proc: (False, None), quiet=quiet)
    assert ok is False


def test_wrong_model_id_left_up_with_warning(adapter):
    quiet, down = {}, [0]
    ok = bw.run_cycle(probe=_probe(False), safe=_safe(True),
                      down_fails=down, spawn=_spawn([]),
                      ready=lambda proc: (True, "checkpoint-9999"),
                      quiet=quiet)
    # up but the WRONG brain: left running (never kills), reported True
    # so the cycle does not spam - the next cycle's probe re-checks.
    assert ok is True


def test_trailing_retry_only_when_card_frees(adapter):
    recorded, quiet, down = [], {}, [0]
    # down while busy x2
    for _ in range(2):
        bw.run_cycle(probe=_probe(False), safe=_safe(False),
                     down_fails=down, spawn=_spawn(recorded), quiet=quiet)
    assert recorded == []
    # card frees -> revival fires
    bw.run_cycle(probe=_probe(False), safe=_safe(True),
                 down_fails=down, spawn=_spawn(recorded),
                 ready=lambda proc: (True, "checkpoint-3873"), quiet=quiet)
    assert len(recorded) == 1
    assert recorded[0].name == "checkpoint-3873"


# ------------------------------------------------------- promote helpers

def test_promoted_adapter_reads_real_json(adapter):
    assert bw.promoted_adapter() == adapter


def test_brain_up_false_on_garbage():
    up, mid = bw.brain_up(port=59999, timeout=1)
    assert up is False and mid is None


# --------------------------------------------------------- guards/daemon

def test_kill_switch_exits_zero(adapter, monkeypatch, capsys):
    monkeypatch.setenv("AALI_BRAIN_WATCHDOG_OFF", "1")
    rc = bw.main.__wrapped__() if hasattr(bw.main, "__wrapped__") else None
    # simpler: call the real main with --once via argv
    monkeypatch.setattr(sys, "argv", ["aali_brain_watchdog.py", "--once"])
    rc = bw.main()
    assert rc == 0


def test_pidlock_blocks_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(bw, "pid_running", lambda pid: True)
    bw.PIDLOCK.write_text("999999", encoding="ascii")
    assert bw.acquire_single_instance() is False


def test_pidlock_allows_new_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(bw, "pid_running", lambda pid: False)
    bw.PIDLOCK.write_text("999999", encoding="ascii")
    assert bw.acquire_single_instance() is True
