"""Regression: the brain must serve the PROMOTED model, not the fallback card.

2026-10-02 incident: every ask on :5055 answered with the deterministic
"direct-command mode" card and the SSE said ``provider: scratch_model``. Chain
of causes, in the order agent_loop tries providers:

1. ``_remote_brain_available()`` — a stale ``AALI_REMOTE_BRAIN_URL`` inherited
   from a 2026-09-14 debug shell pointed at a host that is gone.
2. ``promoted_own_model()`` — returned None because that same shell had
   ``AALI_OWN_MODEL=0``, so the LIVE promoted brain on :20129
   (checkpoint-3933) was skipped silently.
3. ``_ollama_available()`` — false (Ollama's models were deleted 2026-10-01).
4. ``model/scratch/final.pt`` — does not exist -> ``_local_agent_loop``'s
   deterministic command mode. That card, for everything.

Nothing in the server was broken; the LAUNCHER had stopped declaring what the
runtime needs. ``scripts/start_app.bat`` now restores it from files on disk,
and these tests hold that line.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "file-agent"))

import agent_loop  # noqa: E402

START_BAT = REPO / "scripts" / "start_app.bat"
PROMOTED = Path(os.getenv("AALI_PROMOTED_FILE", "D:/hwk-data/soup/promoted.json"))
MASTER_KEY = Path("D:/hwk-data/aali_master_key.txt")
HEALTH = "http://127.0.0.1:5055/api/health"


@pytest.fixture(scope="module")
def launcher() -> str:
    return START_BAT.read_text(encoding="utf-8", errors="replace")


# ————— the launcher must declare the brain-critical env —————

def test_launcher_serves_the_promoted_own_model(launcher):
    """AALI_OWN_MODEL=0 is a debugging kill-switch, never the shipped default."""
    assert "set AALI_OWN_MODEL=1" in launcher


def test_launcher_disables_a_remote_brain_that_can_dead_end(launcher):
    """A stale/unreachable remote URL costs a failed probe per ask; a URL that
    resolves back to THIS box after the tunnel cut-over would self-loop."""
    assert "set AALI_REMOTE_BRAIN_URL=" in launcher
    assert "set AALI_REMOTE_BRAIN=0" in launcher


def test_launcher_restores_key_mode_from_the_key_file(launcher):
    """Without a key app.py binds 127.0.0.1 only and the tunnel dies — and the
    key must be read WITHOUT echoing it into a log or a console."""
    assert "aali_master_key.txt" in launcher
    assert "set /p AALI_API_KEY=<" in launcher
    assert "set AALI_API_KEY=" not in launcher.replace(
        "set /p AALI_API_KEY=<", "")


def test_launcher_keeps_the_owner_workspace_and_commands(launcher):
    assert "set AGENT_WORKSPACE=D:\\hwk-projects" in launcher
    assert "set HWK_ALLOW_COMMANDS=1" in launcher


# ————— the kill switch itself still works —————

def test_own_model_kill_switch_still_honoured(tmp_path, monkeypatch):
    marker = tmp_path / "promoted.json"
    marker.write_text(json.dumps({"base_url": "http://127.0.0.1:20129/v1"}),
                      encoding="utf-8")
    monkeypatch.setattr(agent_loop, "PROMOTED_FILE", marker)
    monkeypatch.delenv("AALI_OWN_MODEL", raising=False)
    monkeypatch.delenv("AALI_OWN_MODEL_URL", raising=False)
    assert agent_loop.promoted_own_model() == {
        "base_url": "http://127.0.0.1:20129/v1",
        "model": agent_loop.OWN_MODEL_NAME,
    }
    monkeypatch.setenv("AALI_OWN_MODEL", "0")
    assert agent_loop.promoted_own_model() is None


def test_a_missing_promoted_file_is_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_loop, "PROMOTED_FILE", tmp_path / "nope.json")
    monkeypatch.delenv("AALI_OWN_MODEL", raising=False)
    assert agent_loop.promoted_own_model() is None


# ————— the live canary (skipped when the server is not this box) —————

def _live_models() -> dict | None:
    try:
        with urllib.request.urlopen(HEALTH, timeout=4) as response:
            if response.status != 200:
                return None
    except Exception:  # noqa: BLE001 - not running here (CI, Pi)
        return None
    if not (PROMOTED.is_file() and MASTER_KEY.is_file()):
        return None
    key = MASTER_KEY.read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        "http://127.0.0.1:5055/api/system/models",
        headers={"X-API-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read())
    except Exception:  # noqa: BLE001 - gated or unreachable
        return None


def test_live_server_serves_the_promoted_brain_not_the_fallback_card():
    """The canary that would have caught the 2026-10-02 incident."""
    if not PROMOTED.is_file():
        pytest.skip("no promoted.json on this machine")
    data = _live_models()
    if data is None:
        pytest.skip("the :5055 server is not running here")
    assert data["own_model_enabled"] is True, (
        "AALI_OWN_MODEL=0 is set in the running server: the promoted brain on "
        ":20129 is being skipped and every ask falls into the deterministic "
        "command mode. Restart it via scripts\\aali_autostart.bat.")
    assert data["serving"]["provider"] == "aali_own"