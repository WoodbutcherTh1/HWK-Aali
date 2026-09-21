"""Headless tests for the in-shell first-run config screen (Q4 UI half).

Pure-logic contracts: page rendering (prefill rules, secret placeholders),
form validation (required fields, ws/wss scheme, keep-on-empty secrets),
the API bridge's save path (persisted through NodeConfig, honest errors),
and the daemon CLI wiring (--shell tokenless opens the screen; --config-ui
forces it; the screen's saved values feed run_shell).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "file-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

from aali_node import config_screen as CS  # noqa: E402
from aali_node import daemon as ND  # noqa: E402
from aali_node.node_config import (  # noqa: E402
    NodeConfig, NodeConfigError, load_node_config)


# ---------------------------------------------------------------------------
# build_config_page — prefill rules
# ---------------------------------------------------------------------------

def test_page_empty_shows_no_prefill_and_no_secret_placeholder():
    page = CS.build_config_page(None)
    assert 'value=""' in page          # hub empty
    assert 'placeholder=""' in page    # no keep-placeholder anywhere
    assert "configured" not in page


def test_page_prefills_nonsecrets_but_never_secrets():
    page = CS.build_config_page({
        "hub_url": "ws://hub:8080", "workspace": "D:/ws",
        "update_hub": "http://hub:8080",
        "has_token": True, "has_update_key": True,
        "allow_commands": False,
    })
    assert 'value="ws://hub:8080"' in page
    assert 'value="D:/ws"' in page
    assert 'value="http://hub:8080"' in page
    # secrets NEVER prefilled — placeholder is the keep marker
    assert 'id="token"' in page and 'value="eyJ' not in page
    assert CS._KEEP_PLACEHOLDER in page
    assert "__ALLOW_COMMANDS__" not in page  # unchecked rendered
    assert "checked" not in page.split('id="allow_commands"')[1].split(">")[0]


def test_page_never_leaks_a_real_secret_value():
    page = CS.build_config_page({
        "hub_url": "ws://h", "has_token": True,
        "has_update_key": True,
    })
    for secret in ("secret-token-value", "sk-", "Bearer "):
        assert secret not in page


def test_page_allow_commands_checked_when_true():
    page = CS.build_config_page({"allow_commands": True})
    checkbox = page.split('id="allow_commands"')[1].split(">")[0]
    assert "checked" in checkbox


# ---------------------------------------------------------------------------
# validate_config_form — the server-side gate
# ---------------------------------------------------------------------------

def _form(**over):
    base = {"hub_url": "ws://hub:8080", "token": "tok-123",
            "workspace": "", "update_hub": "", "update_key": "",
            "allow_commands": True}
    base.update(over)
    return base


class _Keep:
    def __init__(self, token="", update_key=""):
        self.token = token
        self.update_key = update_key


def test_valid_minimal_form_builds_nodeconfig(tmp_path):
    cfg = CS.validate_config_form(_form(), default_workspace=str(tmp_path))
    assert cfg.hub_url == "ws://hub:8080"
    assert cfg.token == "tok-123"
    assert cfg.workspace == str(tmp_path)


def test_missing_hub_url_refused():
    with pytest.raises(NodeConfigError, match="hub_url is required"):
        CS.validate_config_form(_form(hub_url=""))


def test_http_scheme_refused():
    with pytest.raises(NodeConfigError, match="ws:// or wss://"):
        CS.validate_config_form(_form(hub_url="http://hub:8080"))


def test_missing_token_refused_without_keep():
    with pytest.raises(NodeConfigError, match="token is required"):
        CS.validate_config_form(_form(token=""))


def test_token_too_long_refused():
    with pytest.raises(NodeConfigError, match="unreasonably long"):
        CS.validate_config_form(_form(token="t" * 4097))


def test_keep_on_empty_token_uses_caller_value():
    cfg = CS.validate_config_form(_form(token=""), keep=_Keep(token="saved"))
    assert cfg.token == "saved"


def test_typed_token_beats_keep():
    cfg = CS.validate_config_form(_form(token="new"), keep=_Keep(token="old"))
    assert cfg.token == "new"


def test_keep_on_empty_update_key_and_hub_requires_key():
    with pytest.raises(NodeConfigError, match="needs update_key"):
        CS.validate_config_form(_form(update_hub="http://u", update_key=""),
                                keep=_Keep(update_key=""))
    cfg = CS.validate_config_form(
        _form(update_hub="http://u", update_key=""),
        keep=_Keep(update_key="saved-key"))
    assert cfg.update_key == "saved-key"


def test_update_hub_without_any_key_refused_even_with_keep_empty():
    with pytest.raises(NodeConfigError, match="needs update_key"):
        CS.validate_config_form(_form(update_hub="http://u"),
                                keep=_Keep(update_key=""))


def test_non_bool_allow_commands_refused():
    with pytest.raises(NodeConfigError, match="boolean"):
        CS.validate_config_form(_form(allow_commands="yes"))


def test_non_dict_form_refused():
    with pytest.raises(NodeConfigError):
        CS.validate_config_form(["not", "a", "dict"])


# ---------------------------------------------------------------------------
# ConfigScreenApi — save path (real persistence, no webview)
# ---------------------------------------------------------------------------

def test_api_submit_saves_and_returns_ok(tmp_path):
    api = CS.ConfigScreenApi(tmp_path / "cfg.json",
                             default_workspace=str(tmp_path / "ws"))
    res = json.loads(api.submit(json.dumps(_form())))
    assert res["ok"] is True
    assert api.saved is not None
    saved = load_node_config(tmp_path / "cfg.json")
    assert saved.hub_url == "ws://hub:8080"
    assert saved.token == "tok-123"


def test_api_submit_error_does_not_save(tmp_path):
    api = CS.ConfigScreenApi(tmp_path / "cfg.json")
    res = json.loads(api.submit(json.dumps(_form(hub_url=""))))
    assert res["ok"] is False
    assert "hub_url" in res["error"]
    assert not (tmp_path / "cfg.json").exists()


def test_api_bad_json_is_honest_error(tmp_path):
    api = CS.ConfigScreenApi(tmp_path / "cfg.json")
    res = json.loads(api.submit("{not json"))
    assert res["ok"] is False


def test_api_cancel_marks_cancelled(tmp_path):
    api = CS.ConfigScreenApi(tmp_path / "cfg.json")
    api.cancel()  # no window bound — must not raise
    assert api.cancelled is True
    assert api.saved is None


def test_api_page_uses_prefill_and_keep(tmp_path):
    api = CS.ConfigScreenApi(
        tmp_path / "cfg.json",
        prefill={"hub_url": "ws://p:1", "workspace": "W",
                 "update_hub": "http://u", "allow_commands": False},
        keep_token="have", keep_update_key="have2")
    page = api.page()
    assert 'value="ws://p:1"' in page
    assert CS._KEEP_PLACEHOLDER in page
    # and submit with empty secrets keeps them (the browser echoes the
    # prefilled update_hub back in the form)
    res = json.loads(api.submit(json.dumps(
        _form(token="", update_key="", update_hub="http://u"))))
    assert res["ok"] is True
    saved = load_node_config(tmp_path / "cfg.json")
    assert saved.token == "have"
    assert saved.update_key == "have2"
    assert saved.update_hub == "http://u"


def test_api_saved_config_round_trips_through_daemon_precedence(tmp_path):
    """The screen's file must satisfy the daemon's --config loader."""
    api = CS.ConfigScreenApi(tmp_path / "cfg.json",
                             default_workspace=str(tmp_path / "ws"))
    api.submit(json.dumps(_form()))
    from aali_node.node_config import default_config_path  # noqa: F401
    cfg = load_node_config(tmp_path / "cfg.json")
    assert cfg.allow_commands is True
    assert cfg.hub_url == "ws://hub:8080"


# ---------------------------------------------------------------------------
# Daemon CLI wiring — --shell tokenless opens the screen
# ---------------------------------------------------------------------------

class _FakeApi:
    """Config screen double: records the call, "returns" the saved cfg."""

    def __init__(self, saved, calls, **k):
        self.saved = saved
        self.calls = calls
        calls.append(k)

    @classmethod
    def install(cls, saved, calls):
        def _factory(config_path=None, **kwargs):
            cls(saved, calls, **kwargs)
            return saved  # run_config_screen returns the NodeConfig
        return _factory


def test_daemon_shell_tokenless_uses_config_screen(monkeypatch, tmp_path):
    for var in ("AALI_NODE_TOKEN", "AALI_HUB_URL", "AALI_NODE_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)
    calls: list = []
    saved = NodeConfig({"hub_url": "ws://screen:9", "token": "scr-tok",
                        "workspace": str(tmp_path / "sw")})
    monkeypatch.setattr("aali_node.config_screen.run_config_screen",
                        _FakeApi.install(saved, calls))
    # main() imports run_shell from the shell module at call time — patch
    # THERE (patching ND.run_shell would miss the from-import)
    monkeypatch.setattr("aali_node.shell.run_shell", lambda *a, **k: 0)
    rc = ND.main(["--shell"])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["keep_token"] == ""
    assert calls  # screen was consulted


def test_daemon_shell_tokenless_screen_cancelled_is_honest_rc2(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        "aali_node.config_screen.run_config_screen",
        _FakeApi.install(None, []))
    rc = ND.main(["--shell"])
    assert rc == 2


def test_daemon_config_ui_forces_screen_and_keeps_secrets(
        monkeypatch, tmp_path):
    """--config-ui with a CLI token: empty secret fields keep it."""
    calls: list = []
    saved = NodeConfig({"hub_url": "ws://screen:9", "token": "kept",
                        "workspace": str(tmp_path / "sw"),
                        "allow_commands": False})
    monkeypatch.setattr("aali_node.config_screen.run_config_screen",
                        _FakeApi.install(saved, calls))
    monkeypatch.setattr("aali_node.shell.run_shell", lambda *a, **k: 0)
    rc = ND.main(["--shell", "--config-ui", "--hub", "ws://cli:1",
                  "--token", "cli-tok"])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["keep_token"] == "cli-tok"
    assert calls[0]["prefill"]["hub_url"] == "ws://cli:1"
    assert calls[0]["prefill"]["allow_commands"] is True


def test_daemon_shell_with_token_skips_screen(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr("aali_node.config_screen.run_config_screen",
                        _FakeApi.install(None, calls))
    monkeypatch.setattr("aali_node.shell.run_shell", lambda *a, **k: 0)
    rc = ND.main(["--shell", "--hub", "ws://h:1", "--token", "t",
                  "--workspace", str(tmp_path)])
    assert rc == 0
    assert calls == []  # screen never opened


def test_daemon_no_token_headless_still_honest_rc2():
    assert ND.main([]) == 2


def test_packaging_copies_new_modules():
    """The smoke installer + spec must carry the new modules (lesson:
    a build that misses a lazily-imported module boots fine and dies at
    first use)."""
    smoke = (ROOT / "scripts" / "smoke_aali_node.py").read_text(
        encoding="utf-8")
    assert '"node_config.py"' in smoke and '"config_screen.py"' in smoke
    spec = (ROOT / "aali-node.spec").read_text(encoding="utf-8")
    assert "aali_node.node_config" in spec
    assert "aali_node.config_screen" in spec
