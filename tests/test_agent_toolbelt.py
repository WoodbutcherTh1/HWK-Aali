"""Tests for the 2026-09-23 agent toolbelt extensions.

Owner request: give Aali the high-frequency tools the big agents ship and
teach him the right way to use them. These tests pin the mechanics; the
"right way" lives in the tool descriptions + SYSTEM_PROMPT paragraphs.

CPU-only, tmp workspaces, NO network (recall_search's fetcher is injected),
and the screenshot tool is NEVER executed here (it would capture the real
owner's screen) — only its gating is tested.

Run:
    .venv/Scripts/python -m pytest tests/test_agent_toolbelt.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent import agent_tools, file_tools  # noqa: E402
from file_agent.file_tools import FileAgentError  # noqa: E402


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    out = tmp_path / "workspace"
    out.mkdir()
    return out


def _run(name: str, args: dict, ws: Path) -> dict:
    result = file_tools.execute_tool(name, args, ws)
    assert result.get("ok"), f"{name} failed: {result.get('error')}"
    return result["result"]


def _run_err(name: str, args: dict, ws: Path, expect: str) -> dict:
    # execute_tool's real contract: expected errors come back as
    # {ok: False, error: {type: file_operation_error, message: ...}}
    result = file_tools.execute_tool(name, args, ws)
    assert result.get("ok") is False, f"{name} should have failed: {result}"
    error = result.get("error") or {}
    assert expect in str(error.get("message", "")), error
    return error


# ---------------------------------------------------------------------------
# registry + teaching surfaces
# ---------------------------------------------------------------------------

def test_new_tools_are_registered() -> None:
    names = {d["function"]["name"] for d in file_tools.get_tool_definitions()}
    for tool in ("create_artifact", "write_excel", "read_excel", "create_plot",
                 "diff_files", "todo_plan", "recall_search", "screenshot"):
        assert tool in names, f"{tool} missing from definitions"
        assert tool in file_tools.TOOL_EXECUTION, f"{tool} missing execution target"


def test_screenshot_is_confirm_gated_and_guest_blocked() -> None:
    # privacy: native confirm like printing, hard-blocked for remote guests
    assert file_tools.confirm_required("screenshot", {}) is True
    import agent_loop  # noqa: E402
    assert "screenshot" in agent_loop._GUEST_BLOCKED_TOOLS


def test_tool_descriptions_teach_the_right_way() -> None:
    # the "teach him how to use it right" requirement: every new definition
    # carries usage guidance, not just a name
    descs = {d["function"]["name"]: d["function"]["description"]
             for d in file_tools.get_tool_definitions()}
    assert "RIGHT way" in descs["write_excel"]
    assert "web_search first" in descs["recall_search"]
    assert "EXPLICITLY" in descs["screenshot"].upper()
    assert "never invent" in descs["create_plot"].lower()
    assert "multi-step" in descs["todo_plan"]


# ---------------------------------------------------------------------------
# todo_plan — persistent step list
# ---------------------------------------------------------------------------

def test_todo_plan_add_list_complete(ws: Path) -> None:
    _run("todo_plan", {"action": "add", "task": "gather data"}, ws)
    _run("todo_plan", {"action": "add", "task": "write report"}, ws)
    listed = _run("todo_plan", {"action": "list"}, ws)
    assert listed["total"] == 2
    assert listed["plan"][0]["status"] == "pending"
    _run("todo_plan", {"action": "complete", "index": 0}, ws)
    listed = _run("todo_plan", {"action": "list"}, ws)
    assert listed["plan"][0]["status"] == "done"
    assert listed["plan"][1]["status"] == "pending"
    # persisted across calls in the same workspace
    assert (ws / ".aali-plan.json").exists()


def test_todo_plan_update_rejects_bad_status(ws: Path) -> None:
    _run("todo_plan", {"action": "add", "task": "x"}, ws)
    _run_err("todo_plan", {"action": "update", "index": 0, "status": "nope"}, ws,
             "status must be")


def test_todo_plan_clear(ws: Path) -> None:
    _run("todo_plan", {"action": "add", "task": "x"}, ws)
    result = _run("todo_plan", {"action": "clear"}, ws)
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# excel — real spreadsheets, numbers stay numbers
# ---------------------------------------------------------------------------

def test_write_and_read_excel_roundtrip(ws: Path) -> None:
    rows = [["name", "age", "score"], ["Ali", 30, 91.5], ["سارة", 25, 88.0]]
    out = _run("write_excel", {"path": "report.xlsx", "rows": rows}, ws)
    assert out["rows"] == 3 and out["path"] == "report.xlsx"
    got = _run("read_excel", {"path": "report.xlsx"}, ws)
    assert got["rows"][0] == ["name", "age", "score"]
    assert got["rows"][1][1] == 30            # int survived
    assert got["rows"][2][0] == "سارة"         # Arabic survived


def test_write_excel_refuses_overwrite_without_flag(ws: Path) -> None:
    _run("write_excel", {"path": "r.xlsx", "rows": [[1]]}, ws)
    _run_err("write_excel", {"path": "r.xlsx", "rows": [[2]]}, ws,
             "already exists")
    _run("write_excel", {"path": "r.xlsx", "rows": [[2]], "overwrite": True}, ws)


def test_write_excel_appends_extension(ws: Path) -> None:
    out = _run("write_excel", {"path": "noext", "rows": [["a"], ["b"]]}, ws)
    assert out["path"] == "noext.xlsx"


def test_excel_stays_in_workspace_jail(ws: Path) -> None:
    _run_err("write_excel", {"path": "../escape.xlsx", "rows": [[1]]}, ws, "")


# ---------------------------------------------------------------------------
# create_plot — Pillow-drawn charts
# ---------------------------------------------------------------------------

def test_create_plot_bar_png(ws: Path) -> None:
    out = _run("create_plot", {
        "data": {"labels": ["code", "law", "med"],
                 "series": [{"name": "tokens", "values": [12, 9, 7]}]},
        "output": "chart.png", "kind": "bar", "title": "mix"}, ws)
    path = ws / out["path"]
    assert path.exists() and path.stat().st_size > 1000
    from PIL import Image
    with Image.open(path) as img:
        assert img.size == (800, 500)
        assert img.format == "PNG"


def test_create_plot_line_and_pie(ws: Path) -> None:
    data = {"labels": ["a", "b", "c"], "series": [{"name": "s", "values": [3, 1, 2]}]}
    for kind in ("line", "pie"):
        _run("create_plot", {"data": data, "output": f"{kind}.png", "kind": kind}, ws)
        assert (ws / f"{kind}.png").exists()


def test_create_plot_validates_data(ws: Path) -> None:
    _run_err("create_plot", {"data": {"labels": [], "series": []},
                             "output": "x.png"}, ws, "labels")
    _run_err("create_plot", {"data": {"labels": ["a", "b"],
                                      "series": [{"name": "s", "values": [1]}]},
                             "output": "x.png"}, ws, "match")
    _run_err("create_plot", {"data": {"labels": ["a"],
                                      "series": [{"name": "s", "values": [0]}]},
                             "output": "x.png"}, ws, "zero")


# ---------------------------------------------------------------------------
# create_artifact — shareable single-file output
# ---------------------------------------------------------------------------

def test_artifact_html_fragment_gets_wrapped(ws: Path) -> None:
    out = _run("create_artifact", {"title": "تقرير المبيعات",
                                   "content": "<h1>مرحبا</h1><p>نص</p>"}, ws)
    text = (ws / out["path"]).read_text(encoding="utf-8")
    assert "<!doctype html>" in text.lower()
    assert 'dir="rtl"' in text
    assert "<h1>مرحبا</h1>" in text       # fragment kept inside the template


def test_artifact_full_document_written_verbatim(ws: Path) -> None:
    doc = "<!DOCTYPE html><html><body>full</body></html>"
    out = _run("create_artifact", {"title": "x", "content": doc}, ws)
    assert (ws / out["path"]).read_text(encoding="utf-8") == doc


def test_artifact_markdown(ws: Path) -> None:
    out = _run("create_artifact", {"title": "notes", "content": "# hi",
                                   "artifact_type": "markdown"}, ws)
    assert out["path"].endswith(".md")


def test_artifact_rejects_empty_content(ws: Path) -> None:
    _run_err("create_artifact", {"title": "x", "content": "   "}, ws, "non-empty")


# ---------------------------------------------------------------------------
# diff_files — evidence before editing
# ---------------------------------------------------------------------------

def test_diff_files_reports_changes(ws: Path) -> None:
    (ws / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    (ws / "b.txt").write_text("hello\nthere\n", encoding="utf-8")
    out = _run("diff_files", {"path_a": "a.txt", "path_b": "b.txt"}, ws)
    assert out["added"] == 1 and out["removed"] == 1
    assert "-world" in out["diff"] and "+there" in out["diff"]


def test_diff_files_identical(ws: Path) -> None:
    (ws / "same.txt").write_text("x\n", encoding="utf-8")
    out = _run("diff_files", {"path_a": "same.txt", "path_b": "same.txt"}, ws)
    assert out["identical"] is True


# ---------------------------------------------------------------------------
# recall_search — query-focused reading (fetcher injected, NO network)
# ---------------------------------------------------------------------------

def _fake_fetch(url: str, ws_root) -> dict:
    # NOTE: explicit + between literals - adjacent-literal concatenation binds
    # before *, which silently merged the separators into the * 3 repeats
    return {"ok": True, "result": {"url": url, "text":
        "The train arrived at noon exactly when the station clock struck twelve. " * 2 + "\n\n" +
        "Rocket launch scheduled for Friday evening at the coastal spaceport. " * 2 + "\n\n" +
        "The garden needs watering every single morning during high summer. " * 2}}


def test_recall_search_ranks_passages(ws: Path) -> None:
    with patch.object(agent_tools, "_page_text", _fake_fetch):
        out = _run("recall_search", {"url": "https://x.test/a", "query": "rocket launch"}, ws)
    assert out["passages"], "expected at least one passage"
    assert "Rocket" in out["passages"][0]
    assert "garden" not in out["passages"][0]


def test_recall_search_requires_query(ws: Path) -> None:
    _run_err("recall_search", {"url": "https://x.test/a", "query": "  "}, ws,
             "query must be")


def test_recall_search_rejects_non_http(ws: Path) -> None:
    _run_err("recall_search", {"url": "file:///etc/passwd", "query": "x"}, ws,
             "http(s)")


# ---------------------------------------------------------------------------
# screenshot — gating only; the real capture NEVER runs in tests
# ---------------------------------------------------------------------------

def test_screenshot_never_executed_in_tests_but_gated(ws: Path) -> None:
    # simulate the tool running WITHOUT touching PowerShell or the screen:
    # the gating surface is what tests must pin
    assert "screenshot" in file_tools.CONFIRM_REQUIRED
    assert file_tools.TOOL_EXECUTION["screenshot"] == "client"
    # and the PowerShell script it would run writes INSIDE the resolved path
    assert "{path}" in agent_tools._PS_SCREENSHOT


# ---------------------------------------------------------------------------
# tool_guard alias tripwire stays intact with the bigger registry
# ---------------------------------------------------------------------------

def test_tool_guard_aliases_still_resolve() -> None:
    sys.path.insert(0, str(ROOT / "file-agent"))
    from file_agent import tool_guard
    known = {d["function"]["name"] for d in file_tools.get_tool_definitions()}
    stale = {inv: real for inv, real in tool_guard.ALIASES.items() if real not in known}
    assert not stale, f"stale aliases: {stale}"
