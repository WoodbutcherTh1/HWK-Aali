"""Remote-brain provider (AALI_REMOTE_BRAIN_URL): one Aali server using another
Aali server's OpenAI-compatible /v1 as its conversational brain — the Pi hosting
setup. No third-party AI: Aali as a provider, literally."""

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

import agent_loop  # noqa: E402


@pytest.fixture()
def _clean_remote_env(monkeypatch):
    for var in ("AALI_REMOTE_BRAIN_URL", "AALI_REMOTE_BRAIN_KEY", "AALI_REMOTE_BRAIN"):
        monkeypatch.delenv(var, raising=False)
    # Re-read module constants cleared of env (import-time values from CI env).
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_URL", "", raising=False)
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_KEY", "", raising=False)


def test_disabled_by_default(_clean_remote_env):
    assert agent_loop._remote_brain_available() is None


def test_localhost_never_allowed(_clean_remote_env, monkeypatch):
    """A remote brain pointing at this very process would self-loop — refused."""
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_URL", "http://127.0.0.1:5055", raising=False)
    assert agent_loop._remote_brain_available() is None
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_URL", "http://localhost:5055", raising=False)
    assert agent_loop._remote_brain_available() is None


def test_kill_switch(_clean_remote_env, monkeypatch):
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_URL", "http://192.168.1.10:5055", raising=False)
    monkeypatch.setattr(agent_loop, "AALI_REMOTE_BRAIN", "0", raising=False)
    assert agent_loop._remote_brain_available() is None


def test_unreachable_brain_returns_none(_clean_remote_env, monkeypatch):
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_URL", "http://192.168.1.10:5055", raising=False)
    assert agent_loop._remote_brain_available() is None


def test_live_brain_accepted(_clean_remote_env, monkeypatch):
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_URL", "http://192.168.1.10:5055", raising=False)
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_KEY", "k", raising=False)
    resp = mock.Mock(status_code=200)
    with mock.patch.object(agent_loop.requests, "get", return_value=resp) as g:
        got = agent_loop._remote_brain_available()
    assert got == ("http://192.168.1.10:5055", "k", "aali")
    assert g.call_args.kwargs["timeout"] == 1.5


def test_provider_chain_prefers_remote_brain(_clean_remote_env, monkeypatch):
    """mode=local with a live remote brain calls it, labeled aali_remote."""
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_URL", "http://192.168.1.10:5055", raising=False)
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_KEY", "k", raising=False)
    monkeypatch.setattr(agent_loop, "_remote_brain_available",
                        lambda: ("http://192.168.1.10:5055", "k", "aali"), raising=False)
    with mock.patch.object(agent_loop, "_openai_compat_loop", return_value="تم") as loop, \
         mock.patch.object(agent_loop, "promoted_own_model", return_value=None), \
         mock.patch.object(agent_loop, "_ollama_available", return_value=False):
        out = agent_loop.agent_loop("مرحبا", workspace_root=".", request_id="t", mode="local")
    assert "تم" in out
    assert loop.call_args.kwargs["provider_label"] == "aali_remote"


def test_falls_back_when_remote_brain_fails_midflight(_clean_remote_env, monkeypatch):
    """If the remote brain dies mid-loop, the chain continues locally."""
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_URL", "http://192.168.1.10:5055", raising=False)
    monkeypatch.setattr(agent_loop, "REMOTE_BRAIN_KEY", "k", raising=False)
    monkeypatch.setattr(agent_loop, "_remote_brain_available",
                        lambda: ("http://192.168.1.10:5055", "k", "aali"), raising=False)
    with mock.patch.object(agent_loop, "_openai_compat_loop",
                           side_effect=ConnectionError("dead")), \
         mock.patch.object(agent_loop, "promoted_own_model", return_value=None), \
         mock.patch.object(agent_loop, "_ollama_available", return_value=False), \
         mock.patch.object(agent_loop, "_local_model_loop", return_value="محلي") as local:
        out = agent_loop.agent_loop("مرحبا", workspace_root=".", request_id="t", mode="local")
    assert out == "محلي"
    local.assert_called_once()
