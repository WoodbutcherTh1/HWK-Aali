"""HTTP tests for /api/auth/* and admin gating by account role."""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from file_agent import accounts  # noqa: E402
from file_agent import apikeys  # noqa: E402

MASTER = "test-master-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Patch the module global (NOT os.environ): API_KEY is read at call time,
    # and env vars would leak into every other test module in the suite.
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    accounts._configure(tmp_path / "acc.jsonl")
    apikeys._configure(tmp_path / "keys.jsonl")
    accounts._accounts.clear()
    accounts._pending.clear()
    accounts._sessions.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _signup_and_verify(client, email, password="password123"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    code = r.get_json()["dev_code"]
    v = client.post("/api/auth/verify", json={"email": email, "code": code})
    assert v.status_code == 200
    return v.get_json()


def _login(client, email, password="password123"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]


def _auth_headers(token):
    return {"X-Session-Token": token}


def test_signup_verify_me_flow(client):
    res = _signup_and_verify(client, "flow@example.com")
    assert res["role"] == "user"
    token = _login(client, "flow@example.com")
    me = client.get("/api/auth/me", headers=_auth_headers(token))
    body = me.get_json()
    assert body["ok"] and body["email"] == "flow@example.com" and body["role"] == "user"


def test_account_gets_linked_api_key(client):
    _signup_and_verify(client, "linked@example.com")
    rec = accounts.account("linked@example.com")
    assert rec["key_id"]  # metering key issued automatically


def test_admin_session_opens_admin_surface(client):
    _signup_and_verify(client, "hmam@hwk.team")
    token = _login(client, "hmam@hwk.team")
    r = client.get("/api/admin/keys", headers=_auth_headers(token))
    assert r.status_code == 200, r.get_json()


def test_user_session_blocked_from_admin_surface(client):
    _signup_and_verify(client, "plain@example.com")
    token = _login(client, "plain@example.com")
    r = client.get("/api/admin/keys", headers=_auth_headers(token))
    assert r.status_code == 401


def test_no_token_blocked_from_admin_surface(client):
    assert client.get("/api/admin/keys").status_code == 401


def test_master_key_still_works(client):
    r = client.get("/api/admin/keys", headers={"X-API-Key": MASTER})
    assert r.status_code == 200


def test_admin_accounts_listing_and_delete(client):
    _signup_and_verify(client, "hmam@hwk.team")
    _signup_and_verify(client, "victim@example.com")
    token = _login(client, "hmam@hwk.team")
    listing = client.get("/api/admin/accounts", headers=_auth_headers(token))
    emails = [a["email"] for a in listing.get_json()["accounts"]]
    assert "victim@example.com" in emails and "hmam@hwk.team" in emails
    del_r = client.delete(
        "/api/admin/accounts?email=victim@example.com", headers=_auth_headers(token)
    )
    assert del_r.status_code == 200
    assert accounts.account("victim@example.com") is None


def test_reset_endpoints(client):
    _signup_and_verify(client, "http-reset@example.com")
    rq = client.post("/api/auth/reset-request", json={"email": "http-reset@example.com"})
    code = rq.get_json()["dev_code"]
    rc = client.post(
        "/api/auth/reset-confirm",
        json={"email": "http-reset@example.com", "code": code, "new_password": "brandnew123"},
    )
    assert rc.status_code == 200
    assert _login(client, "http-reset@example.com", "brandnew123")


def test_logout_endpoint(client):
    _signup_and_verify(client, "bye@example.com")
    token = _login(client, "bye@example.com")
    assert client.post("/api/auth/logout", headers=_auth_headers(token)).status_code == 200
    assert client.get("/api/auth/me", headers=_auth_headers(token)).status_code == 401
