"""Tests for file_agent/tool_orchestrator.py (SaaS STEP 1)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent import tool_orchestrator as orch  # noqa: E402
from file_agent.file_tools import (  # noqa: E402
    TOOL_EXECUTION, _FUNCTIONS, confirm_required,
)


# ---------------------------------------------------------------------------
# classification table integrity
# ---------------------------------------------------------------------------
def test_every_registered_tool_is_classified() -> None:
    missing = set(_FUNCTIONS) - set(TOOL_EXECUTION)
    extra = set(TOOL_EXECUTION) - set(_FUNCTIONS)
    assert not missing and not extra, (
        f"TOOL_EXECUTION must classify every registered tool exactly — "
        f"missing: {missing} / extra: {extra}"
    )


def test_classification_counts_match_mission_spec() -> None:
    assert len(orch.server_tools()) == 12
    assert len(orch.client_tools()) == 12
    assert len(orch.both_tools()) == 1
    assert orch.both_tools() == {"memory"}


def test_confirmed_dangerous_tools_set() -> None:
    from file_agent.file_tools import CONFIRM_REQUIRED
    assert CONFIRM_REQUIRED == frozenset({
        "run_command", "delete_file", "move_file", "machine_ops",
    })


# ---------------------------------------------------------------------------
# dispatch — server tools
# ---------------------------------------------------------------------------
def test_server_tool_routes_to_server() -> None:
    for name in ("web_search", "generate_image", "generate_emoji",
                 "fetch_url", "edit_video"):
        d = orch.dispatch(name)
        assert d["route"] == "server"
        assert d["target"] == "server"
        assert d["requires_confirmation"] is False


def test_server_tool_decision_fields() -> None:
    d = orch.dispatch("web_search", args={"query": "hello"})
    assert set(d) == {"tool", "target", "route",
                      "requires_confirmation", "reason"}
    assert isinstance(d["reason"], str) and d["reason"]


# ---------------------------------------------------------------------------
# dispatch — client tools
# ---------------------------------------------------------------------------
def test_client_tool_routes_to_client() -> None:
    for name in ("read_file", "write_file", "run_command", "list_files",
                 "machine_ops"):
        d = orch.dispatch(name)
        assert d["route"] == "client"


def test_client_tool_denied_when_client_disallowed() -> None:
    d = orch.dispatch("read_file", allow_client=False)
    assert d["route"] == "unroutable"
    assert "not allowed" in d["reason"]


def test_dangerous_client_tools_require_confirmation() -> None:
    for name in ("run_command", "delete_file", "move_file", "machine_ops"):
        d = orch.dispatch(name)
        assert d["requires_confirmation"] is True


def test_safe_client_tools_do_not_require_confirmation() -> None:
    for name in ("read_file", "list_files", "search_files",
                 "make_directory"):
        d = orch.dispatch(name)
        assert d["requires_confirmation"] is False


def test_write_file_conditional_confirmation() -> None:
    plain = orch.dispatch("write_file", args={"path": "a.txt", "content": "x"})
    assert plain["requires_confirmation"] is False
    over = orch.dispatch("write_file",
                         args={"path": "a.txt", "content": "x",
                               "overwrite": True})
    assert over["requires_confirmation"] is True


# ---------------------------------------------------------------------------
# dispatch — both / memory
# ---------------------------------------------------------------------------
def test_memory_defaults_to_server_and_prefers_client_on_request() -> None:
    assert orch.dispatch("memory")["route"] == "server"
    assert orch.dispatch("memory", prefer="client")["route"] == "client"


def test_memory_confirmation_follows_context() -> None:
    # memory is never in CONFIRM_REQUIRED — confirmation is False on both
    # routes.
    assert orch.dispatch("memory")["requires_confirmation"] is False
    assert orch.dispatch("memory", prefer="client")["requires_confirmation"] is False


# ---------------------------------------------------------------------------
# dispatch — unknown tools
# ---------------------------------------------------------------------------
def test_unknown_tool_is_unroutable() -> None:
    d = orch.dispatch("paint")  # an observed tool_guard invention
    assert d["route"] == "unroutable"
    assert d["requires_confirmation"] is False


def test_unknown_tool_never_silently_routed() -> None:
    d = orch.dispatch("totally_fake_tool_xyz")
    assert d["route"] == "unroutable"


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------
def test_bad_prefer_raises() -> None:
    with pytest.raises(orch.ToolOrchestratorError):
        orch.dispatch("memory", prefer="both")


def test_empty_tool_name_raises() -> None:
    with pytest.raises(orch.ToolOrchestratorError):
        orch.dispatch("")


def test_non_string_tool_raises() -> None:
    with pytest.raises(orch.ToolOrchestratorError):
        orch.dispatch(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tripwires
# ---------------------------------------------------------------------------
def test_run_command_is_never_server_resident() -> None:
    # Security tripwire: the command runner must never execute on the brain
    # in saas mode — it belongs to the user's machine.
    assert TOOL_EXECUTION["run_command"] == "client"


def test_confirm_required_matches_file_tools() -> None:
    # orchestrator and file_tools must agree on confirmation semantics.
    for name in ("run_command", "delete_file", "move_file", "machine_ops",
                 "read_file", "write_file"):
        assert orch.dispatch(name, args={"overwrite": True})[
            "requires_confirmation"] == confirm_required(name, {"overwrite": True})
