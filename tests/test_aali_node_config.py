"""Tests for aali_node.node_config — the first-run config storage (Q4).

Headless pure-logic tests: parse/validate/persist contracts, POSIX
permission rules (skipped on Windows where they cannot be reproduced),
and the never-log redaction contract. The daemon CLI wiring
(--config / --save-config precedence) is tested in test_aali_node_config_cli.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from aali_node.node_config import (
    NodeConfig,
    NodeConfigError,
    default_config_path,
    load_node_config,
)


def _write(path: Path, values: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def test_minimal_config_happy_path(tmp_path: Path) -> None:
    p = _write(tmp_path / "cfg.json",
               {"hub_url": "wss://hub.example", "token": "jwt"})
    cfg = load_node_config(p)
    assert cfg.hub_url == "wss://hub.example"
    assert cfg.token == "jwt"
    assert cfg.allow_commands is True
    assert cfg.workspace == ""            # daemon applies its own default
    assert cfg.using_token_file is False


def test_rejects_non_json_object(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(NodeConfigError, match="JSON object"):
        load_node_config(p)


def test_rejects_broken_json(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(NodeConfigError, match="not valid JSON"):
        load_node_config(p)


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(NodeConfigError, match="no saved config"):
        load_node_config(tmp_path / "absent.json")


def test_rejects_unknown_keys(tmp_path: Path) -> None:
    p = _write(tmp_path / "cfg.json", {"hub_url": "x", "token": "t",
                                       "sudo": True})
    with pytest.raises(NodeConfigError, match="unknown config keys"):
        load_node_config(p)


def test_rejects_non_string_values(tmp_path: Path) -> None:
    p = _write(tmp_path / "cfg.json", {"hub_url": {"evil": True},
                                       "token": "t"})
    with pytest.raises(NodeConfigError, match="must be a string"):
        load_node_config(p)


def test_rejects_non_bool_allow_commands(tmp_path: Path) -> None:
    p = _write(tmp_path / "cfg.json", {"hub_url": "x", "token": "t",
                                       "allow_commands": "yes"})
    with pytest.raises(NodeConfigError, match="must be a boolean"):
        load_node_config(p)


def test_rejects_token_and_token_file_together() -> None:
    with pytest.raises(NodeConfigError, match="either token or token_file"):
        NodeConfig({"hub_url": "x", "token": "a", "token_file": "b"})


def test_requires_hub_url_and_token(tmp_path: Path) -> None:
    p = _write(tmp_path / "cfg.json", {"token": "t"})
    with pytest.raises(NodeConfigError, match="no hub_url"):
        load_node_config(p)
    p2 = _write(tmp_path / "cfg2.json", {"hub_url": "x"})
    with pytest.raises(NodeConfigError, match="no token"):
        load_node_config(p2)


# ---------------------------------------------------------------------------
# secret files
# ---------------------------------------------------------------------------
def test_token_file_is_loaded_and_stripped(tmp_path: Path) -> None:
    secret = tmp_path / "token.txt"
    secret.write_text("  jwt-from-file  \n", encoding="utf-8")
    cfg = NodeConfig({"hub_url": "x", "token_file": str(secret)})
    assert cfg.token == "jwt-from-file"
    assert cfg.using_token_file is True


def test_missing_token_file_fails_loudly(tmp_path: Path) -> None:
    cfg = NodeConfig({"hub_url": "x", "token_file": str(tmp_path / "no.txt")})
    with pytest.raises(NodeConfigError, match="unreadable"):
        cfg.token


def test_empty_token_file_refused(tmp_path: Path) -> None:
    secret = tmp_path / "token.txt"
    secret.write_text("   \n", encoding="utf-8")
    cfg = NodeConfig({"hub_url": "x", "token_file": str(secret)})
    with pytest.raises(NodeConfigError, match="empty"):
        cfg.token


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission model")
def test_group_readable_token_file_refused_on_posix(tmp_path: Path) -> None:
    secret = tmp_path / "token.txt"
    secret.write_text("jwt", encoding="utf-8")
    os.chmod(secret, 0o644)
    cfg = NodeConfig({"hub_url": "x", "token_file": str(secret)})
    with pytest.raises(NodeConfigError, match="readable by others"):
        cfg.token


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission model")
def test_group_readable_config_refused_on_posix(tmp_path: Path) -> None:
    p = _write(tmp_path / "cfg.json", {"hub_url": "x", "token": "t"})
    os.chmod(p, 0o664)
    with pytest.raises(NodeConfigError, match="readable by others"):
        load_node_config(p)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
def test_save_load_roundtrip_inline_token(tmp_path: Path) -> None:
    cfg = NodeConfig({"hub_url": "wss://hub", "token": "sekrit",
                      "workspace": "C:/ws", "allow_commands": False,
                      "update_hub": "https://hub", "update_key": "vk"})
    p = cfg.save(tmp_path / "sub" / "aali-node.json")
    loaded = load_node_config(p)
    assert loaded.token == "sekrit"
    assert loaded.hub_url == "wss://hub"
    assert loaded.allow_commands is False
    assert loaded.update_key == "vk"
    # the saved JSON never contains a secret-file field by accident
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["token"] == "sekrit"
    assert "token_file" not in data


def test_save_keeps_token_file_indirection(tmp_path: Path) -> None:
    secret = tmp_path / "token.txt"
    secret.write_text("jwt", encoding="utf-8")
    cfg = NodeConfig({"hub_url": "x", "token_file": str(secret)})
    p = cfg.save(tmp_path / "cfg.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "token" not in data and "token_file" in data
    assert load_node_config(p).token == "jwt"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission model")
def test_save_is_owner_only_on_posix(tmp_path: Path) -> None:
    p = NodeConfig({"hub_url": "x", "token": "t"}).save(
        tmp_path / "cfg.json")
    assert stat.S_IMODE(p.stat().st_mode) & 0o077 == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission model")
def test_save_refuses_to_loosen_existing_mode(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o666)
    with pytest.raises(NodeConfigError, match="world-readable"):
        NodeConfig({"hub_url": "x", "token": "t"}).save(p)


# ---------------------------------------------------------------------------
# never-log redaction contract
# ---------------------------------------------------------------------------
def test_secrets_view_redacts_everything() -> None:
    cfg = NodeConfig({"hub_url": "x", "token": "sekrit", "update_key": "vk"})
    view = cfg.secrets()
    assert "sekrit" not in json.dumps(view)
    assert "vk" not in json.dumps(view)
    assert view["token"] == "***" and view["update_key"] == "***"

    secret = Path(os.devnull)  # any path works for the flag shape
    cfg2 = NodeConfig({"hub_url": "x", "token_file": str(secret)})
    assert cfg2.secrets()["token"] == "<file>"


def test_to_json_never_leaks_update_key_file(tmp_path: Path) -> None:
    kf = tmp_path / "k.txt"
    kf.write_text("vk", encoding="utf-8")
    cfg = NodeConfig({"hub_url": "x", "token": "t", "update_key_file": str(kf)})
    text = cfg.to_json()
    assert "vk" not in text and "update_key_file" in text


# ---------------------------------------------------------------------------
# default path
# ---------------------------------------------------------------------------
def test_default_path_is_outside_the_install() -> None:
    p = default_config_path()
    assert p.name == "aali-node.json"
    repo = Path(__file__).resolve().parents[1]
    assert repo not in p.resolve().parents or "AaliNode" in str(p) \
        or ".aali-node" in str(p)


def test_concurrent_token_reads_are_cached(tmp_path: Path) -> None:
    secret = tmp_path / "token.txt"
    secret.write_text("jwt", encoding="utf-8")
    cfg = NodeConfig({"hub_url": "x", "token_file": str(secret)})
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(cfg.token))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["jwt"] * 8
