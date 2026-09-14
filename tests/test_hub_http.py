"""HTTP-level tests for the Aali Hub (FastAPI TestClient).

The app is built with a dead brain URL (port 1) so no test ever reaches a
real brain — even if the owner's server is live on :5055.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "file-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("fastapi", reason="FastAPI not installed in this venv")

from fastapi.testclient import TestClient  # noqa: E402

from aali_hub.config import HubConfig  # noqa: E402
from aali_hub.main import create_app  # noqa: E402


@pytest.fixture()
def hub(tmp_path: Path) -> tuple[TestClient, HubConfig]:
    config = HubConfig(
        dev_mode=True,
        jwt_secret="jwt-secret-0123456789abcdef-0123456789",
        brain_token="brain-token-0123456789abcdef-012345",
        master_key="master-key-0123456789abcdef-0123456789",
        db_path=tmp_path / "hub.db",
        log_dir=tmp_path / "logs",
        update_dir=tmp_path / "updates",
        brain_url="http://127.0.0.1:1",  # dead port on purpose
    )
    app = create_app(config)
    return TestClient(app), config


def _register(client: TestClient, email: str = "user@example.com",
              password: str = "password123") -> dict:
    resp = client.post("/api/auth/register",
                       json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _login(client: TestClient, email: str = "user@example.com",
           password: str = "password123") -> str:
    resp = client.post("/api/auth/login",
                       json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
def test_health_reports_disconnected_state(hub) -> None:
    client, _ = hub
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["brain_connected"] is False
    assert body["nodes_connected"] == 0
    assert body["circuit_open"] is False


def test_metrics_endpoint(hub) -> None:
    client, _ = hub
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "aali_hub_up 1" in resp.text


# ---------------------------------------------------------------------------
# auth endpoints
# ---------------------------------------------------------------------------
def test_register_login_me_round_trip(hub) -> None:
    client, _ = hub
    out = _register(client)
    assert out["user"]["email"] == "user@example.com"
    assert out["user"]["role"] == "free_user"
    token = _login(client)
    resp = client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "user@example.com"
    assert body["usage"]["requests"] == 0


def test_register_validation_and_duplicate(hub) -> None:
    client, _ = hub
    assert client.post("/api/auth/register",
                       json={"email": "bad", "password": "password123"}
                       ).status_code == 400
    assert client.post("/api/auth/register",
                       json={"email": "a@b.co", "password": "short"}
                       ).status_code == 400
    _register(client, "a@b.co")
    assert client.post("/api/auth/register",
                       json={"email": "a@b.co", "password": "password123"}
                       ).status_code == 400


def test_login_wrong_password_401(hub) -> None:
    client, _ = hub
    _register(client)
    resp = client.post("/api/auth/login",
                       json={"email": "user@example.com",
                             "password": "wrong-password"})
    assert resp.status_code == 401


def test_me_requires_token(hub) -> None:
    client, _ = hub
    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# admin gating
# ---------------------------------------------------------------------------
def test_admin_endpoints_reject_free_users(hub) -> None:
    client, _ = hub
    _register(client)
    token = _login(client)
    assert client.get("/api/admin/users",
                      headers={"Authorization": f"Bearer {token}"}
                      ).status_code == 403
    assert client.get("/api/admin/users").status_code == 401


def test_admin_owner_manages_users_models_audit(hub) -> None:
    client, config = hub
    # seed an owner directly (bootstrap path: first owner out-of-band)
    state = client.app.state.hub
    owner = state.users_db.create_user("owner@hwk.local", "ownerpass123",
                                       role="owner", email_verified=True)
    _register(client, "someone@example.com")
    token = _login(client, "owner@hwk.local", "ownerpass123")
    hdrs = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/admin/users", headers=hdrs)
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["users"]]
    assert "owner@hwk.local" in emails and "someone@example.com" in emails

    # find the free user and promote them
    target = next(u for u in resp.json()["users"]
                  if u["email"] == "someone@example.com")
    resp = client.post(f"/api/admin/users/{target['id']}/role",
                       json={"role": "pro_user"}, headers=hdrs)
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "pro_user"

    # model registry CRUD
    resp = client.post(
        "/api/admin/models",
        json={"model_id": "aali", "version": "ckpt-3873",
              "path": "D:/hwk-models/tuned/checkpoint-3873",
              "format": "safetensors", "context_length": 4096},
        headers=hdrs)
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/admin/models/aali/ckpt-3873/promote",
                       headers=hdrs)
    assert resp.status_code == 200
    assert resp.json()["model"]["promoted"] is True
    resp = client.get("/api/admin/models", headers=hdrs)
    assert len(resp.json()["models"]) == 1

    # audit captured the actions
    resp = client.get("/api/admin/audit", headers=hdrs)
    actions = [e["action"] for e in resp.json()["events"]]
    assert "admin_set_role" in actions and "admin_promote_model" in actions


# ---------------------------------------------------------------------------
# /v1 openai-compatible surface
# ---------------------------------------------------------------------------
def test_v1_models_with_api_key(hub) -> None:
    client, _ = hub
    _register(client)
    state = client.app.state.hub
    user = state.users_db.get_user_by_email("user@example.com")
    key = state.users_db.create_api_key(user["id"], name="test")
    resp = client.get("/v1/models",
                      headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert any(m["id"] == "aali" for m in body["data"])


def test_v1_models_rejects_bad_key(hub) -> None:
    client, _ = hub
    resp = client.get("/v1/models",
                      headers={"Authorization": "Bearer aali-not-a-key"})
    assert resp.status_code == 401
    assert client.get("/v1/models").status_code == 401


def test_v1_chat_completions_dead_brain_openai_error(hub) -> None:
    client, _ = hub
    _register(client)
    state = client.app.state.hub
    user = state.users_db.get_user_by_email("user@example.com")
    key = state.users_db.create_api_key(user["id"])
    resp = client.post("/v1/chat/completions",
                       json={"model": "aali",
                             "messages": [{"role": "user",
                                           "content": "hi"}]},
                       headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 502
    err = resp.json()["error"]
    assert err["type"] == "api_error"
    assert err["code"] == "brain_unavailable"


def test_api_ask_dead_brain_502(hub) -> None:
    client, _ = hub
    _register(client)
    token = _login(client)
    resp = client.post("/api/ask", json={"message": "hello"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 502
    assert "brain" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# websocket transport
# ---------------------------------------------------------------------------
def test_ws_brain_hello_ping_pong_round_trip(hub) -> None:
    client, _ = hub
    sys.path.insert(0, str(ROOT / "file-agent"))
    from file_agent import protocol as P

    with client.websocket_connect("/ws/brain") as ws:
        ws.send_json({"type": "hello", "role": "brain",
                      "token": "brain-token-0123456789abcdef-012345"})
        leg_key = P.derive_session_key(
            "brain-token-0123456789abcdef-012345", "brain-leg")
        ws.send_json(P.sign_message(P.make_ping(), leg_key))
        pong = ws.receive_json()
        P.parse_message(pong, leg_key, expect=P.TYPE_PONG)


def test_ws_rejects_bad_token(hub) -> None:
    client, _ = hub
    from fastapi import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/brain") as ws:
            ws.send_json({"type": "hello", "role": "brain",
                          "token": "wrong-token"})
            ws.receive_json()


def test_ws_health_reflects_brain_connection(hub) -> None:
    client, _ = hub
    with client.websocket_connect("/ws/brain") as ws:
        ws.send_json({"type": "hello", "role": "brain",
                      "token": "brain-token-0123456789abcdef-012345"})
        # connection registered → /health sees the brain
        body = client.get("/health").json()
        assert body["brain_connected"] is True
        ws.send_json({"type": "bye"})  # broker drops unknown types safely
    # after disconnect the brain is gone
    body = client.get("/health").json()
    assert body["brain_connected"] is False
