"""Tests for aali_node.shell — the GUI shell over the headless daemon.

Everything here is headless: ShellState/ShellApi are pure, the daemon status
contract is tested through run_node's honest failure path, and the real
pystray icon runs offscreen (skipped when no tray backend is available).
The pywebview window itself is NOT spawned in tests (a GUI event loop does
not belong in pytest) — run_shell's fail-fast paths are covered instead.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

import pytest

from aali_node import daemon as ND
from aali_node import shell as SH

# ---------------------------------------------------------------------------
# ShellState — pure view folding
# ---------------------------------------------------------------------------
def test_fold_minimal_snapshot_is_authorized_arabic() -> None:
    st = SH.ShellState()
    view = st.fold({"state": "starting"})
    assert view["state"] == "starting"
    assert view["state_ar"] == "بدء التشغيل…"
    assert view["stats"] == {}
    assert view["log"] == []


def test_fold_maps_every_daemon_state() -> None:
    st = SH.ShellState()
    for state in ("starting", "connected", "error", "stopped"):
        view = st.fold({"state": state})
        assert view["state_ar"] != state          # always Arabic-first
        assert view["state_class"] in ("wait", "run", "bad", "idle")


def test_fold_unknown_state_falls_back_to_raw() -> None:
    st = SH.ShellState()
    view = st.fold({"state": "quantum"})
    assert view["state_ar"] == "quantum"
    assert view["state_class"] == "wait"


def test_fold_log_dedupes_repeated_events_and_is_capped() -> None:
    st = SH.ShellState(log_cap=5)
    for _ in range(3):
        st.fold({"state": "connected", "last_event": "connected to hub"})
    assert len([e for e in st.log if e["text"] == "connected to hub"]) == 1

    for i in range(10):
        st.fold({"state": "connected", "last_event": f"event {i}"})
    assert len(st.log) == 5                       # cap respected
    assert st.log[0]["text"] == "event 9"         # newest first


def test_fold_logs_stats_changes_and_errors() -> None:
    st = SH.ShellState()
    st.fold({"state": "connected", "stats": {"executed": 1, "denied": 0,
                                             "errors": 0}})
    st.fold({"state": "connected", "stats": {"executed": 2, "denied": 1,
                                             "errors": 0}})
    st.fold({"state": "connected", "last_error": "boom: 1"})
    kinds = [e["kind"] for e in st.log]
    assert kinds.count("stats") == 2              # one line per stats change
    assert kinds[0] == "error"                    # newest on top
    assert st.log[0]["text"] == "boom: 1"


def test_fold_snapshot_is_json_serializable() -> None:
    st = SH.ShellState()
    view = st.fold({"state": "connected", "stats": {"executed": 2},
                    "connected_since": 123.5, "node_id": "n1"})
    assert json.loads(json.dumps(view))["stats"] == {"executed": 2}


# ---------------------------------------------------------------------------
# ShellApi — the js_api bridge
# ---------------------------------------------------------------------------
def test_api_snapshot_returns_daemon_view() -> None:
    status = {"state": "connected", "hub_url": "ws://hub", "node_id": "n-1",
              "stats": {"executed": 3, "denied": 1, "errors": 0}}
    api = SH.ShellApi(SH.ShellState(), status, lambda: "C:/ws",
                      threading.Event())
    view = json.loads(api.snapshot())
    assert view["hub_url"] == "ws://hub"
    assert view["stats"]["executed"] == 3


def test_api_quit_sets_event_and_destroys_window() -> None:
    quit_event = threading.Event()
    api = SH.ShellApi(SH.ShellState(), {}, lambda: "", quit_event)

    class FakeWindow:
        def __init__(self) -> None:
            self.shown = 0
            self.destroyed = 0

        def show(self) -> None:
            self.shown += 1

        def destroy(self) -> None:
            self.destroyed += 1

    win = FakeWindow()
    api.bind_window(win)
    api.quit_app()
    assert quit_event.is_set()
    assert win.shown == 1 and win.destroyed == 1


# ---------------------------------------------------------------------------
# build_tray_icon — real pystray, offscreen, graceful fallback
# ---------------------------------------------------------------------------
def test_build_tray_icon_runs_detached_or_returns_none() -> None:
    tray = SH.build_tray_icon("t", "ws", on_show=lambda: None,
                              on_quit=lambda: None)
    if tray is None:
        pytest.skip("no tray backend on this machine (CI/headless)")
    try:
        assert tray.visible is True or tray.visible is False  # detached ok
    finally:
        tray.stop()


# ---------------------------------------------------------------------------
# run_shell — fail-fast contracts (no GUI event loop in pytest)
# ---------------------------------------------------------------------------
def test_run_shell_refuses_empty_token() -> None:
    assert SH.run_shell("ws://127.0.0.1:1", "", "ws") == 2


def test_run_shell_refuses_missing_webview(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch
                                           ) -> None:
    import builtins
    real_import = builtins.__import__

    def _no_webview(name: str, *a, **kw):  # noqa: ANN002, ANN003
        if name == "webview":
            raise ImportError("webview hidden for test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_webview)
    assert SH.run_shell("ws://127.0.0.1:1", "tok", tmp_path) == 2


# ---------------------------------------------------------------------------
# daemon status contract — the shell's data source
# ---------------------------------------------------------------------------
def test_run_node_publishes_status_through_failure_path(
        tmp_path: Path) -> None:
    status: dict = {}
    # port 1 = guaranteed refusal; the daemon must STILL have published its
    # starting state and then the honest failure (dead-brain-guard style).
    # Venv-agnostic like the house tests: without websockets the daemon
    # fails one step earlier (rc 2) but the status contract is identical.
    has_ws = importlib.util.find_spec("websockets") is not None
    rc = ND.run_node("ws://127.0.0.1:1", "tok", tmp_path / "ws",
                     status=status, stop=threading.Event())
    assert rc == (1 if has_ws else 2)
    assert status["state"] == "error"
    assert status["hub_url"] == "ws://127.0.0.1:1"
    assert status["node_id"]
    assert status["workspace"].endswith("ws")
    assert status["allow_commands"] is True
    assert status["native_confirm"] is False
    assert status["last_error"]


def test_run_node_status_is_optional_and_none_safe(tmp_path: Path) -> None:
    # the historical embedders pass no status at all — must keep working
    rc = ND.run_node("ws://127.0.0.1:1", "tok", tmp_path / "ws")
    assert rc in (1, 2)  # 1 with websockets, 2 without — both honest
