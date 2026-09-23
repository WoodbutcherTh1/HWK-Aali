"""Tests for spawn_agents (2026-09-23, owner request: parallel sub-agents).

agent_loop.agent_loop is faked at the module attribute — no model call, no
GPU, no network. The tests pin the CONTRACT the exam will grade:

- up to 4 tasks fan out to parallel sub-agents, each in agents/agent-N
- every fake sees its own task + its own workspace + the SHORT leash
- a guest parent's policy is inherited by every child (never "auto")
- sub-agents cannot spawn sub-agents (depth guard, teaching refusal)
- the 4-agent cap fails loudly; dependent guidance lives in the tool def
- results merge into one dict with ok/spawned/succeeded/results
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import agent_loop  # noqa: E402
from file_agent import agent_tools  # noqa: E402
from file_agent.file_tools import FileAgentError  # noqa: E402


class FakeLoop:
    """Replaces agent_loop.agent_loop: records calls, returns canned text."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, task, workspace_root, **kwargs):
        self.calls.append({
            "task": task,
            "workspace_root": str(workspace_root),
            **kwargs,
        })
        return f"done: {task[:20]}"


@pytest.fixture()
def fake_loop(monkeypatch):
    fake = FakeLoop()
    monkeypatch.setattr(agent_loop, "agent_loop", fake)
    # clean contextvar state per test
    token = agent_loop._SUB_AGENT_POLICY.set(None)
    yield fake
    agent_loop._SUB_AGENT_POLICY.reset(token)


def test_four_tasks_fan_out_parallel(tmp_path, fake_loop):
    tasks = ["task one alpha", "task two beta", "task three gamma", "task four delta"]
    result = agent_tools.spawn_agents(tasks, tmp_path, request_id="req-test-1")
    assert result["ok"] is True
    assert result["spawned"] == 4 and result["succeeded"] == 4
    assert [r["reply"] for r in result["results"]] == [f"done: {t[:20]}" for t in tasks]
    # each sub-agent got its own sandboxed subfolder (calls complete in
    # parallel, so order by workspace, not arrival)
    ordered = sorted(fake_loop.calls, key=lambda c: c["workspace_root"])
    for i, call in enumerate(ordered):
        assert call["workspace_root"] == str(tmp_path / "agents" / f"agent-{i + 1}")
        assert call["max_iterations"] == agent_tools.MAX_SUB_AGENT_TURNS
        assert call["print_final"] is False
    assert (tmp_path / "agents" / "agent-1").is_dir()
    assert (tmp_path / "agents" / "agent-4").is_dir()


def test_cap_refuses_five_tasks(tmp_path, fake_loop):
    with pytest.raises(FileAgentError, match="4-agent cap"):
        agent_tools.spawn_agents(["t"] * 5, tmp_path)
    assert fake_loop.calls == []


def test_type_guard_rejects_string(tmp_path):
    with pytest.raises(FileAgentError):
        agent_tools.spawn_agents("not a list", tmp_path)  # type: ignore[arg-type]


def test_guest_policy_inherited_by_children(tmp_path, fake_loop, monkeypatch):
    token = agent_loop._SUB_AGENT_POLICY.set("guest")
    try:
        result = agent_tools.spawn_agents(["guest task"], tmp_path)
    finally:
        agent_loop._SUB_AGENT_POLICY.reset(token)
    assert result["ok"] is True
    assert fake_loop.calls[0]["policy"] == "guest"


def test_always_ask_policy_inherited(tmp_path, fake_loop):
    token = agent_loop._SUB_AGENT_POLICY.set("always_ask")
    try:
        agent_tools.spawn_agents(["careful task"], tmp_path)
    finally:
        agent_loop._SUB_AGENT_POLICY.reset(token)
    assert fake_loop.calls[0]["policy"] == "always_ask"


def test_recursion_guard_sub_agents_cannot_spawn(tmp_path, fake_loop):
    # Simulate being inside a sub-agent worker (depth already 1 in THIS thread)
    agent_tools._SPAWN_DEPTH.value = 1
    try:
        result = agent_tools.spawn_agents(["nested"], tmp_path)
    finally:
        agent_tools._SPAWN_DEPTH.value = 0
    assert result["ok"] is False
    assert result["refused"] == "recursion"
    assert "لا أستطيع توليد" in result["results"][0]["error"]
    assert fake_loop.calls == []  # nothing ran


def test_crashing_sub_agent_does_not_kill_fanout(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(agent_loop, "agent_loop", boom)
    result = agent_tools.spawn_agents(["a", "b"], tmp_path)
    assert result["ok"] is False
    assert result["succeeded"] == 0
    assert all("RuntimeError" in r["error"] for r in result["results"])


def test_empty_tasks_rejected(tmp_path):
    with pytest.raises(FileAgentError):
        agent_tools.spawn_agents([], tmp_path)
    with pytest.raises(FileAgentError):
        agent_tools.spawn_agents(["  ", ""], tmp_path)


def test_registry_and_definitions_wire_spawn_agents():
    from file_agent import file_tools
    names = [d["function"]["name"] for d in file_tools.get_tool_definitions()]
    assert "spawn_agents" in names
    assert file_tools._FUNCTIONS["spawn_agents"] is not None
    assert file_tools.TOOL_EXECUTION["spawn_agents"] == "server"
    # guests never spawn agents (guest gate in agent_loop)
    assert "spawn_agents" in agent_loop._GUEST_BLOCKED_TOOLS


def test_spawn_agents_is_dangerous_gated_like_run_command():
    # confirmation-policy surface (always_ask policy asks before spawning
    # a fleet of loops) — parity with run_command.
    assert agent_loop._is_dangerous_call("spawn_agents", {"tasks": ["x"]}) is True
