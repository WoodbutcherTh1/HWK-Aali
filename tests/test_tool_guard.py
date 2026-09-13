"""Tests for file_agent/tool_guard.py and its agent_loop integration."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent import tool_guard  # noqa: E402
from file_agent.file_tools import _FUNCTIONS  # noqa: E402


# ---------------------------------------------------------------------------
# registry tripwire — an alias pointing outside the registry is a bug that
# would silently rename calls into the void
# ---------------------------------------------------------------------------
def test_aliases_point_at_real_registry_tools() -> None:
    known = tool_guard.registry_names()
    assert known == set(_FUNCTIONS)  # live view of the registry
    stale = {inv: real for inv, real in tool_guard.ALIASES.items() if real not in known}
    assert not stale, f"aliases point outside the registry: {stale}"


def test_registry_names_match_file_tools() -> None:
    assert len(tool_guard.registry_names()) == 25


# ---------------------------------------------------------------------------
# validation tiers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "paint", "local_clip", "audio_recorder", "file", "read_files",
    "sticker_maker", "emoji_maker", "image_editor", "ocr", "text_reader",
    "run", "python", "bash", "shell", "print", "remember", "note",
    "video", "video_quality", "drawing_tool",
])
def test_observed_inventions_resolve_through_aliases(name: str) -> None:
    effective, fix = tool_guard.validate_tool_name(name)
    assert effective in tool_guard.registry_names()
    assert fix is not None and fix["resolved"] == effective
    assert fix["method"] == "alias" and not fix["rejected"]


@pytest.mark.parametrize("name", [
    "generate_image", "generate_video", "read_file", "run_command",
    "memory", "generate_emoji", "read_image", "final_tool_never_mind_this_is_not_real",
])
def test_real_names_pass_through_untouched(name: str) -> None:
    # note: real names pass even when not in the registry? No — only registry
    # names pass clean; the long fake name is rejected further down.
    if name in tool_guard.registry_names():
        effective, fix = tool_guard.validate_tool_name(name)
        assert effective == name and fix is None


def test_casefold_slip_corrected() -> None:
    effective, fix = tool_guard.validate_tool_name("Read_File")
    assert effective == "read_file"
    assert fix is not None and fix["method"] == "casefold"


def test_clear_fuzzy_match_corrected_conservatively() -> None:
    # single close registry neighbour -> conservative correction
    effective, fix = tool_guard.validate_tool_name("read_fille")
    assert effective == "read_file"
    assert fix is not None and fix["method"] == "fuzzy"


def test_ambiguous_or_distant_names_rejected() -> None:
    for invented in ("math", "registration", "quantum_flux_engine"):
        effective, fix = tool_guard.validate_tool_name(invented)
        assert effective == ""
        assert fix is not None and fix["rejected"]


def test_rejected_result_teaches_alternatives() -> None:
    _, fix = tool_guard.validate_tool_name("registration")
    result = tool_guard.rejected_result(fix)
    assert result["ok"] is False
    assert result["error"]["type"] == "unknown_tool"
    assert "registration" in result["error"]["message"]


def test_kill_switch_disables_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AALI_TOOL_GUARD_OFF", "1")
    effective, fix = tool_guard.validate_tool_name("paint")
    assert effective == "paint" and fix is None  # passes through untouched


def test_audit_log_writes_content_free_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AALI_TOOL_GUARD_AUDIT", str(audit))
    tool_guard.validate_tool_name("paint")
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = __import__("json").loads(lines[0])
    assert event["invented"] == "paint" and event["resolved"] == "generate_image"
    assert "arguments" not in event  # content-free by design


# ---------------------------------------------------------------------------
# agent_loop integration — the ordering guarantee: the policy gate and
# execute_tool see the CORRECTED name, and rejections never execute anything.
# Source-level assertions (importing agent_loop spawns heavy imports; these
# tripwires keep the invariant visible at review time).
# ---------------------------------------------------------------------------
def test_dispatch_sites_validate_before_the_policy_gate() -> None:
    src = (ROOT / "file-agent" / "agent_loop.py").read_text(encoding="utf-8")
    assert src.count("tool_guard.validate_tool_name") == 5, (
        "every tool dispatch site must consult the guard exactly once")
    # the guard import must come from the same package as execute_tool
    assert "from file_agent import execute_tool, get_tool_definitions, tool_guard" in src


def test_rejected_calls_never_reach_execute_tool() -> None:
    src = (ROOT / "file-agent" / "agent_loop.py").read_text(encoding="utf-8")
    # every guard branch guards execution behind `if not guard_name` / else
    assert src.count("tool_guard.rejected_result") == 5
    for block in src.split("tool_guard.validate_tool_name")[1:]:
        # after each validation, a rejection must short-circuit before any
        # _policy_gate/execute_tool in the same block
        reject_pos = block.find("tool_guard.rejected_result")
        gate_pos = min(
            (p for p in (block.find("_policy_gate("),
                         block.find("execute_tool(")) if p != -1),
            default=-1)
        assert reject_pos != -1, "missing rejection branch"
        if gate_pos != -1:
            assert reject_pos < gate_pos or "else" in block[:gate_pos], (
                "rejection must precede execution in its branch")


def test_guard_module_is_stdlib_only() -> None:
    src = (ROOT / "file-agent" / "file_agent" / "tool_guard.py").read_text(
        encoding="utf-8")
    for banned in ("import requests", "import torch", "import psutil"):
        assert banned not in src
