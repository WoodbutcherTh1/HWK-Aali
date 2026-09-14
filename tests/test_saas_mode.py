"""Tests for SaaS mode (STEP 4): per-user scoping on the brain.

Covers aali_hub/user_context (mode + user roots), the execute_tool saas gate
(client tools can never run on the brain), per-user memory scoping via the
contextvar, the scope-aware memory cache, and the app.py endpoints.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "file-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

from aali_hub import user_context as uc  # noqa: E402
from aali_hub.user_context import (  # noqa: E402
    SaasModeError, aali_mode, user_memory_dir, user_root, validate_user_id,
)
from file_agent import memory as aali_memory  # noqa: E402

# app.py needs the env pinned BEFORE import (same contract as test_hwk.py).
os_environ_backup = dict(__import__("os").environ)
__import__("os").environ.setdefault("AALI_OLLAMA", "0")
__import__("os").environ.setdefault("AALI_OWN_MODEL", "0")
import app as app_module  # noqa: E402
import file_agent.file_tools as file_tools  # noqa: E402


# ---------------------------------------------------------------------------
# user_context
# ---------------------------------------------------------------------------
def test_mode_defaults_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AALI_MODE", raising=False)
    assert aali_mode() == "local"


def test_mode_saas_and_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AALI_MODE", "saas")
    assert aali_mode() == "saas"
    monkeypatch.setenv("AALI_MODE", "cloud")
    with pytest.raises(SaasModeError):
        aali_mode()


def test_validate_user_id_strict() -> None:
    assert validate_user_id("u-abcd1234") == "u-abcd1234"
    for bad in ("", "../etc", "a/b", r"a\\b", "..", ".", "a b", "x" * 100,
                "-leading", "workspace", "uploads"):
        with pytest.raises(SaasModeError):
            validate_user_id(bad)


def test_user_roots_live_under_base(monkeypatch: pytest.MonkeyPatch,
                                    tmp_path: Path) -> None:
    monkeypatch.setenv("AALI_USERS_DIR", str(tmp_path))
    root = user_root("u-abcd1234")
    assert root == tmp_path / "u-abcd1234"
    assert user_memory_dir("u-abcd1234") == root / "memory"
    # traversal ids are rejected before any path math happens
    with pytest.raises(SaasModeError):
        user_root("../escape")


# ---------------------------------------------------------------------------
# execute_tool saas gate
# ---------------------------------------------------------------------------
@pytest.fixture()
def fake_web_search(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def _fake(**kwargs):  # matches the ToolFunction contract
        calls.append(kwargs)
        return {"result": "fake-search-ok"}

    monkeypatch.setitem(file_tools._FUNCTIONS, "web_search", _fake)
    return calls


def test_saas_gate_blocks_client_tools_on_brain(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AALI_MODE", "saas")
    out = file_tools.execute_tool("read_file", {"path": "x.txt"}, tmp_path)
    assert out["ok"] is False
    assert out["error"]["type"] == "client_tool_remote"
    # the file was never touched
    assert not (tmp_path / "x.txt").exists()


def test_saas_gate_passes_server_tools(monkeypatch: pytest.MonkeyPatch,
                                       tmp_path: Path,
                                       fake_web_search) -> None:
    monkeypatch.setenv("AALI_MODE", "saas")
    out = file_tools.execute_tool("web_search", {"query": "x"}, tmp_path)
    assert out["ok"] is True and out["result"] == {"result": "fake-search-ok"}
    assert len(fake_web_search) == 1


def test_local_mode_has_no_gate(monkeypatch: pytest.MonkeyPatch,
                                tmp_path: Path,
                                fake_web_search) -> None:
    monkeypatch.delenv("AALI_MODE", raising=False)
    out = file_tools.execute_tool("web_search", {"query": "x"}, tmp_path)
    assert out["ok"] is True  # server tool executes locally, as always
    # and client tools still execute on the brain in local mode:
    w = file_tools.execute_tool(
        "write_file", {"path": "loc.txt", "content": "hi"}, tmp_path)
    assert w["ok"] is True
    assert (tmp_path / "loc.txt").exists()


# ---------------------------------------------------------------------------
# memory scope (contextvar) + cache isolation
# ---------------------------------------------------------------------------
def test_memory_scope_precedence(monkeypatch: pytest.MonkeyPatch,
                                 tmp_path: Path) -> None:
    monkeypatch.setenv("AALI_MEMORY_DIR", str(tmp_path / "env"))
    token = aali_memory.set_memory_scope(tmp_path / "scoped")
    try:
        assert aali_memory.memory_dir() == tmp_path / "scoped"
        # explicit argument still wins over the scope
        assert aali_memory.memory_dir(tmp_path / "explicit") == \
            tmp_path / "explicit"
    finally:
        aali_memory.reset_memory_scope(token)
    assert aali_memory.memory_dir() == tmp_path / "env"  # scope released


def test_memory_entries_are_scoped(tmp_path: Path) -> None:
    token = aali_memory.set_memory_scope(tmp_path / "userA" / "memory")
    try:
        aali_memory.remember("user A likes tea", kind="preference")
    finally:
        aali_memory.reset_memory_scope(token)
    token = aali_memory.set_memory_scope(tmp_path / "userB" / "memory")
    try:
        found = aali_memory.recall(query="tea")["result"]["entries"]
        assert found == []  # user B sees nothing of user A
        aali_memory.remember("user B likes coffee", kind="preference")
    finally:
        aali_memory.reset_memory_scope(token)
    a_file = tmp_path / "userA" / "memory" / aali_memory.MEMORY_FILE_NAME
    b_file = tmp_path / "userB" / "memory" / aali_memory.MEMORY_FILE_NAME
    assert a_file.exists() and b_file.exists()
    assert "tea" in a_file.read_text(encoding="utf-8")
    assert "coffee" in b_file.read_text(encoding="utf-8")
    assert "tea" not in b_file.read_text(encoding="utf-8")


def test_memory_cache_never_leaks_across_scopes(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import agent_loop as al
    monkeypatch.setenv("AALI_MEMORY_DIR", str(tmp_path / "envstore"))
    al._invalidate_memory_cache()
    al._memory_block()
    key_a = al._MEMORY_CACHE["path"]
    token = aali_memory.set_memory_scope(tmp_path / "other" / "memory")
    try:
        block = al._memory_block()
        key_b = al._MEMORY_CACHE["path"]
        assert key_b != key_a  # scope change forces a re-render
        assert str(tmp_path / "other" / "memory") in key_b
    finally:
        aali_memory.reset_memory_scope(token)
    assert block is not None


# ---------------------------------------------------------------------------
# app.py endpoints
# ---------------------------------------------------------------------------
@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    app_module._sessions.clear()
    app_module._sessions_loaded = True
    with app_module.app.test_client() as c:
        yield c


def test_health_reports_mode(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AALI_MODE", raising=False)
    body = client.get("/api/health").get_json()
    assert body["ok"] is True and body["mode"] == "local"
    monkeypatch.setenv("AALI_MODE", "saas")
    body = client.get("/api/health").get_json()
    assert body["mode"] == "saas"
    monkeypatch.delenv("AALI_MODE", raising=False)


def test_brain_status_counts_only(client) -> None:
    body = client.get("/api/brain/status").get_json()
    assert body["ok"] is True
    assert body["mode"] in ("local", "saas")
    assert isinstance(body["active_sessions"], int)
    assert "gpu" in body and "model" in body


def test_user_info_unscoped_locally(client) -> None:
    body = client.get("/api/user/info").get_json()
    assert body["ok"] is True
    assert body["scoped"] is False
    assert body["user_id"] is None


def test_user_info_scoped_in_saas_mode(client,
                                       monkeypatch: pytest.MonkeyPatch,
                                       tmp_path: Path) -> None:
    monkeypatch.setenv("AALI_MODE", "saas")
    monkeypatch.setenv("AALI_USERS_DIR", str(tmp_path))
    body = client.get("/api/user/info",
                      headers={"X-Aali-User": "u-abcd1234"}).get_json()
    assert body["scoped"] is True and body["user_id"] == "u-abcd1234"
    assert str(tmp_path / "u-abcd1234") in body["workspace"]
    # invalid header → not scoped (header is never trusted blindly)
    body = client.get("/api/user/info",
                      headers={"X-Aali-User": "../evil"}).get_json()
    assert body["scoped"] is False


def test_ask_scopes_workspace_and_memory(client, monkeypatch: pytest.MonkeyPatch,
                                         tmp_path: Path) -> None:
    captured = {}

    def spy_agent_loop(message, workspace_root, **kwargs):
        captured["workspace"] = str(workspace_root)
        captured["memory_dir"] = (
            str(kwargs["memory_dir"]) if kwargs.get("memory_dir") else None)
        return "تم"

    monkeypatch.setattr(app_module, "agent_loop", spy_agent_loop)
    monkeypatch.setenv("AALI_MODE", "saas")
    monkeypatch.setenv("AALI_USERS_DIR", str(tmp_path))

    # no header → local defaults
    resp = client.post("/api/ask", json={"message": "hi", "sid": "s1"})
    assert resp.status_code == 200
    assert captured["memory_dir"] is None
    assert captured["workspace"] == str(app_module.WORKSPACE_ROOT)

    # valid header → per-user scope
    resp = client.post("/api/ask", json={"message": "hi", "sid": "s2"},
                       headers={"X-Aali-User": "u-abcd1234"})
    assert resp.status_code == 200
    assert str(tmp_path / "u-abcd1234") in captured["workspace"]
    assert str(tmp_path / "u-abcd1234" / "memory") in captured["memory_dir"]

    monkeypatch.delenv("AALI_MODE", raising=False)
    monkeypatch.delenv("AALI_USERS_DIR", raising=False)
