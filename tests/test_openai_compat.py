"""OpenAI-compatible /v1 surface: model discovery + chat completions shape.

n8n / OpenRouter-style clients / Hugging Face tools talk to Aali through
/v1/chat/completions (OpenAI wire format, stream and non-stream). These tests
pin the surface: /v1/models lists Aali, requests validate cleanly, and the
response has the OpenAI shape — without calling the real brain (the agent
loop is monkeypatched so tests stay fast and deterministic).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))

import app as app_module  # noqa: E402
app = app_module.app


def _fake_agent_loop(message, workspace_root, *, history=None, **kwargs):
    return "مرحباً من آلي"


def test_v1_models_lists_aali(monkeypatch) -> None:
    client = app.test_client()
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.get_json()
    ids = {m["id"] for m in body["data"]}
    assert {"aali", "aali-local"} <= ids


def test_v1_chat_completions_shape(monkeypatch) -> None:
    monkeypatch.setattr("app.agent_loop", _fake_agent_loop)
    client = app.test_client()
    payload = {"model": "aali",
               "messages": [{"role": "user", "content": "مرحباً"}]}
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "aali"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "مرحباً من آلي" in body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "stop"


def test_v1_requires_user_turn() -> None:
    client = app.test_client()
    resp = client.post("/v1/chat/completions",
                       json={"model": "aali", "messages": []})
    assert resp.status_code == 400


def test_v1_stream_returns_sse_chunks(monkeypatch) -> None:
    monkeypatch.setattr("app.agent_loop", _fake_agent_loop)
    client = app.test_client()
    payload = {"model": "aali", "stream": True,
               "messages": [{"role": "user", "content": "عد من ١ إلى ٣"}]}
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    text = resp.get_data(as_text=True)
    assert "data: [DONE]" in text
    assert "chat.completion.chunk" in text
    # the streamed chunk must carry the reply content
    assert "مرحباً من آلي" in text


def test_v1_stream_keeps_guest_policy_for_remote_keys(monkeypatch) -> None:
    """Remote non-admin keys over a tunnel are forced to guest policy."""
    # app.py reads AALI_API_KEY at import time; simulate a key-mode server.
    import tempfile
    store = Path(tempfile.mkdtemp()) / "keys.jsonl"
    monkeypatch.setattr(app_module, "API_KEY", "v1-guest-master")
    monkeypatch.setattr(app_module.apikeys, "DEFAULT_STORE", store)
    monkeypatch.setattr(app_module.apikeys, "_store_path", store)
    _, plaintext = app_module.apikeys.issue_key(label="guest-1",
                                                created_by="admin")
    monkeypatch.setattr(app_module.apikeys, "authenticate",
                        lambda key: {"is_admin": False, "key_id": "guest-1"}
                        if key == plaintext else None)
    called: dict = {}

    def spy_agent_loop(message, workspace_root, *, history=None, **kwargs):
        called["policy"] = kwargs.get("policy")
        return "ok"

    monkeypatch.setattr("app.agent_loop", spy_agent_loop)
    client = app.test_client()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "aali", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer " + plaintext,
                 "X-Forwarded-For": "8.8.8.8"},
    )
    assert resp.status_code == 200
    assert called.get("policy") == "guest"