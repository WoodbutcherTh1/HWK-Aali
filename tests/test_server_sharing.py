"""Server-sharing security: guest policy gate + fail-safe bind host.

When the owner shares Aali with friends (LAN or tunnel), remote non-admin
keys must be forced into the server-enforced guest policy — the client-sent
policy/confirm fields are NOT a security boundary — and an unauthenticated
server must never bind beyond localhost.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

import agent_loop as al  # noqa: E402


# ------------------------------------------------------------ guest policy
def test_guest_blocks_run_command_even_when_confirmed() -> None:
    out = al._policy_gate("run_command", {"command": "dir"}, "guest",
                          confirmed=True, gate_state=None)
    assert out is not None and out["ok"] is False
    assert out["error"]["type"] == "guest_forbidden"


def test_guest_blocks_memory_and_deletes() -> None:
    for tool, args in (("memory", {"action": "save", "text": "x"}),
                       ("delete_file", {"path": "a.txt"}),
                       ("machine_ops", {"action": "kill_process"})):
        out = al._policy_gate(tool, args, "guest", confirmed=True, gate_state=None)
        assert out is not None and out["ok"] is False, tool


def test_guest_allows_read_write_search_and_media() -> None:
    for tool, args in (("read_file", {"path": "a.txt"}),
                       ("write_file", {"path": "a.txt", "content": "hi"}),
                       ("search_files", {"pattern": "*.py"}),
                       ("read_image", {"path": "a.png"}),
                       ("web_search", {"query": "python"})):
        assert al._policy_gate(tool, args, "guest", confirmed=False,
                               gate_state=None) is None, tool


def test_guest_gate_sets_gate_state_for_needs_confirm() -> None:
    state: dict = {}
    al._policy_gate("run_command", {"command": "whoami"}, "guest",
                    confirmed=True, gate_state=state)
    assert state.get("blocked") is True
    assert state.get("tool") == "run_command"


def test_owner_policies_unchanged() -> None:
    # auto/aggressive still bypass; always_ask still gates dangerous calls.
    assert al._policy_gate("run_command", {"command": "dir"}, "auto",
                           confirmed=False, gate_state=None) is None
    out = al._policy_gate("run_command", {"command": "dir"}, "always_ask",
                          confirmed=False, gate_state=None)
    assert out is not None and out["error"]["type"] == "confirmation_required"


# -------------------------------------------------------------- bind host
def test_bind_host_localhost_without_key(monkeypatch) -> None:
    import app as flask_app
    monkeypatch.delenv("AALI_BIND", raising=False)
    monkeypatch.setattr(flask_app, "API_KEY", "")
    assert flask_app._bind_host() == "127.0.0.1"


def test_bind_host_public_only_with_key(monkeypatch) -> None:
    import app as flask_app
    monkeypatch.delenv("AALI_BIND", raising=False)
    monkeypatch.setattr(flask_app, "API_KEY", "aali-secret")
    assert flask_app._bind_host() == "0.0.0.0"


def test_bind_env_override(monkeypatch) -> None:
    import app as flask_app
    monkeypatch.setenv("AALI_BIND", "192.168.1.9")
    monkeypatch.setattr(flask_app, "API_KEY", "")
    assert flask_app._bind_host() == "192.168.1.9"
