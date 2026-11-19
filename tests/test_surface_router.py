"""Tests for the intent/surface router (switch_surface + SURFACE_SWITCH)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent import file_tools  # noqa: E402


def test_switch_surface_accepts_the_closed_enum(tmp_path: Path) -> None:
    for surface in ("chat", "studio", "graphics", "control"):
        out = file_tools.execute_tool(
            "switch_surface", {"surface_name": surface, "reason": "test"},
            tmp_path)
        assert out["ok"] is True
        assert out["result"]["surface"] == surface
        assert out["result"]["switched"] is True


def test_switch_surface_refuses_an_invented_surface(tmp_path: Path) -> None:
    out = file_tools.execute_tool(
        "switch_surface", {"surface_name": "hologram", "reason": "?"}, tmp_path)
    assert out["ok"] is False
    assert "Unknown surface" in out["error"]["message"]


def test_switch_surface_is_registered_server_side() -> None:
    assert file_tools.TOOL_EXECUTION["switch_surface"] == "server"
    definition = next(
        item for item in file_tools.get_tool_definitions()
        if item["function"]["name"] == "switch_surface")
    props = definition["function"]["parameters"]["properties"]
    assert props["surface_name"]["enum"] == ["chat", "studio", "graphics", "control"]
    assert definition["function"]["parameters"]["required"] == ["surface_name"]


def test_surface_orchestration_is_in_the_system_prompt() -> None:
    import agent_loop as al

    assert "SURFACE ORCHESTRATION" in al.SYSTEM_PROMPT
    assert "switch_surface" in al.SYSTEM_PROMPT
    assert "SURFACE_SWITCH" in al.SYSTEM_PROMPT


def test_note_surface_emits_a_dedicated_event(tmp_path: Path) -> None:
    import agent_log
    import agent_loop as al

    request_id = "surface-test-1"
    queue = agent_log.subscribe(request_id)
    state: dict = {}
    try:
        al._note_surface(
            request_id, "switch_surface",
            {"ok": True, "result": {"surface": "studio", "reason": "refactor"}},
            state)
        event = queue.get(timeout=2)
        assert event["event"] == "surface_switch"
        assert event["surface"] == "studio"
        assert event["reason"] == "refactor"
        assert state["surface"]["surface"] == "studio"
    finally:
        agent_log.unsubscribe(request_id)


def test_note_surface_ignores_failed_calls() -> None:
    import agent_loop as al

    state: dict = {}
    al._note_surface("no-event", "switch_surface",
                     {"ok": False, "error": {"message": "no"}}, state)
    assert "surface" not in state


def test_stream_emits_surface_switch_and_proposal_events(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_log
    import app as app_mod

    monkeypatch.setattr(app_mod, "WORKSPACE_ROOT", tmp_path)

    def fake_loop(message, workspace_root, **kwargs):
        rid = kwargs.get("request_id")
        agent_log.log_event(rid, "surface_switch", surface="studio",
                            reason="complex coding")
        agent_log.log_event(rid, "proposal_created", proposal_id="abc123",
                            path="src/app.py", tool="write_file",
                            diff="--- before\n+++ after")
        return "review the proposal"

    monkeypatch.setattr(app_mod, "agent_loop", fake_loop)
    client = app_mod.app.test_client()
    response = client.post("/api/ask/stream", json={
        "message": "refactor the app", "sid": "surface-stream"})
    text = response.get_data(as_text=True)
    assert "event: SURFACE_SWITCH" in text
    assert '"surface": "studio"' in text
    assert "event: proposal_created" in text
    assert '"proposal_id": "abc123"' in text
    assert "event: done" in text
