"""Tests for the daemon CLI's first-run config wiring (Q4).

Contracts:
- precedence is flag > env > saved config > builtin default;
- an explicit --no-commands ALWAYS wins over the saved allow_commands;
- --save-config persists owner-only and continues serving;
- a broken config fails with exit 2 and an actionable message;
- secrets never appear in stdout/stderr (the CLI prints paths, not tokens).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from aali_node import daemon as ND


@pytest.fixture()
def offline(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture run_node's kwargs instead of touching the network."""
    calls: list[dict] = []

    def fake_run_node(hub_url, token, workspace_root, **kwargs):
        calls.append({"hub_url": hub_url, "token": token,
                      "workspace": str(workspace_root), **kwargs})
        return 0

    monkeypatch.setattr(ND, "run_node", fake_run_node)
    return calls


def _write_config(path: Path, values: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_flag_beats_config(tmp_path: Path, offline,
                           monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path / "cfg.json",
                  {"hub_url": "wss://from-config", "token": "cfg-token"})
    rc = ND.main(["--config", str(tmp_path / "cfg.json"),
                  "--hub", "wss://from-flag", "--token", "flag-token",
                  "--workspace", str(tmp_path)])
    assert rc == 0
    assert offline[0]["hub_url"] == "wss://from-flag"
    assert offline[0]["token"] == "flag-token"


def test_config_supplies_missing_values(tmp_path: Path, offline) -> None:
    _write_config(tmp_path / "cfg.json",
                  {"hub_url": "wss://hub", "token": "cfg-token",
                   "workspace": str(tmp_path / "ws"),
                   "allow_commands": False})
    rc = ND.main(["--config", str(tmp_path / "cfg.json")])
    assert rc == 0
    assert offline[0]["hub_url"] == "wss://hub"
    assert offline[0]["token"] == "cfg-token"
    assert offline[0]["allow_commands"] is False
    assert offline[0]["workspace"] == str(tmp_path / "ws")


def test_env_beats_config_but_flag_beats_env(
        tmp_path: Path, offline, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path / "cfg.json",
                  {"hub_url": "wss://from-config", "token": "t"})
    monkeypatch.setenv("AALI_HUB_URL", "wss://from-env")
    rc = ND.main(["--config", str(tmp_path / "cfg.json"),
                  "--workspace", str(tmp_path)])
    assert offline[0]["hub_url"] == "wss://from-env"
    rc = ND.main(["--config", str(tmp_path / "cfg.json"),
                  "--hub", "wss://from-flag", "--workspace", str(tmp_path)])
    assert offline[1]["hub_url"] == "wss://from-flag"


def test_no_commands_flag_wins_over_saved_allow_commands(
        tmp_path: Path, offline) -> None:
    _write_config(tmp_path / "cfg.json",
                  {"hub_url": "wss://hub", "token": "t",
                   "allow_commands": True})
    rc = ND.main(["--config", str(tmp_path / "cfg.json"),
                  "--no-commands", "--workspace", str(tmp_path)])
    assert rc == 0
    assert offline[0]["allow_commands"] is False


def test_saved_false_cannot_be_re_enabled_without_flag(
        tmp_path: Path, offline) -> None:
    """A paranoid saved config stays paranoid: no flag means no commands."""
    _write_config(tmp_path / "cfg.json",
                  {"hub_url": "wss://hub", "token": "t",
                   "allow_commands": False})
    rc = ND.main(["--config", str(tmp_path / "cfg.json"),
                  "--workspace", str(tmp_path)])
    assert rc == 0
    assert offline[0]["allow_commands"] is False


def test_broken_config_exits_2_with_message(tmp_path: Path,
                                            capsys: pytest.CaptureFixture
                                            ) -> None:
    p = tmp_path / "cfg.json"
    p.write_text("{broken", encoding="utf-8")
    rc = ND.main(["--config", str(p)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--config failed" in err


def test_missing_config_exits_2(tmp_path: Path) -> None:
    assert ND.main(["--config", str(tmp_path / "absent.json")]) == 2


def test_config_update_values_flow_through(tmp_path: Path, offline) -> None:
    _write_config(tmp_path / "cfg.json",
                  {"hub_url": "wss://hub", "token": "t",
                   "update_hub": "https://hub-updates",
                   "update_key": "verify-me"})
    rc = ND.main(["--config", str(tmp_path / "cfg.json"),
                  "--workspace", str(tmp_path)])
    assert rc == 0
    assert offline[0]["update_hub"] == "https://hub-updates"
    assert offline[0]["update_key"] == "verify-me"


def test_save_config_writes_owner_only_file_and_continues(
        tmp_path: Path, offline, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "saved" / "aali-node.json"
    monkeypatch.setattr(ND, "_default_config_path", lambda: target)
    rc = ND.main(["--save-config", "--hub", "wss://hub",
                  "--token", "sekrit-jwt", "--workspace", str(tmp_path / "ws"),
                  "--update-hub", "https://u", "--update-key", "vk"])
    assert rc == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["token"] == "sekrit-jwt"      # stored (owner-only file)
    assert data["hub_url"] == "wss://hub"
    # and the same run continued serving with the same values
    assert offline[0]["token"] == "sekrit-jwt"

    # ...and a later --config run can consume it
    rc = ND.main(["--config", str(target), "--workspace", str(tmp_path)])
    assert rc == 0
    assert offline[0]["hub_url"] == "wss://hub"
    assert offline[0]["update_key"] == "vk"


def test_save_config_requires_token(tmp_path: Path,
                                    capsys: pytest.CaptureFixture) -> None:
    rc = ND.main(["--save-config", "--hub", "wss://hub"])
    assert rc == 2
    assert "needs --token" in capsys.readouterr().err


def test_cli_output_never_contains_the_token(
        tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Every failure path prints messages, never secrets."""
    secret = "super-secret-jwt-value"
    _write_config(tmp_path / "cfg.json",
                  {"hub_url": "wss://hub", "token": secret})
    ND.main(["--config", str(tmp_path / "cfg.json")])  # succeeds; no leak
    ND.main(["--config", str(tmp_path / "nope.json")])  # fails; no leak
    ND.main(["--save-config"])                          # fails; no leak
    out = capsys.readouterr().out + capsys.readouterr().err
    assert secret not in out


def test_shell_mode_passes_resolved_values(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run_shell(hub_url, token, workspace_root, **kwargs):
        seen.update(hub=hub_url, token=token, **kwargs)
        return 0

    monkeypatch.setattr("aali_node.shell.run_shell", fake_run_shell)
    _write_config(tmp_path / "cfg.json",
                  {"hub_url": "wss://hub", "token": "cfg-token",
                   "allow_commands": False})
    rc = ND.main(["--shell", "--config", str(tmp_path / "cfg.json")])
    assert rc == 0
    assert seen["hub"] == "wss://hub"
    assert seen["token"] == "cfg-token"
    assert seen["allow_commands"] is False
