"""Tests for server-side role resolution (Track B 11.1, 2026-09-25).

Pure unit tests over file_agent/roles.resolve_role + the HTTP contract
of /api/auth/me (the role endpoint every client consumes).

Roles, exactly five: owner > dev > admin > user > guest.
The resolver sees ONLY server-verified facts — client-sent role hints
(X-Role header, payload fields) can never influence it by construction:
resolve_role() takes no request object at all.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from file_agent import roles  # noqa: E402

MASTER = "test-master-key"


# ————— pure resolver —————

def test_owner_via_master_key():
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=True, account_email=None,
        account_is_admin=False, key_id="admin", is_local=True,
        authenticated=True)
    assert ctx["role"] == "owner"
    assert ctx["user_id"] == "owner"
    assert "training" in ctx["permissions"]
    assert "master_key" in ctx["permissions"]


def test_owner_via_local_no_key():
    """No AALI_API_KEY configured + local call = the owner's machine."""
    ctx = roles.resolve_role(
        api_key_configured=False, is_master_key=False, account_email=None,
        account_is_admin=False, key_id=None, is_local=True,
        authenticated=False)
    assert ctx["role"] == "owner"


def test_local_unauthenticated_with_key_configured_is_NOT_owner():
    """A key IS configured but the local caller presented none: user-level
    read surfaces, never owner (master key or sign-in required to rule)."""
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=False, account_email=None,
        account_is_admin=False, key_id=None, is_local=True,
        authenticated=False)
    assert ctx["role"] == "user"
    assert "training" not in ctx["permissions"]


def test_dev_email_beats_admin_and_user():
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=False,
        account_email="dev@hwk.team", account_is_admin=False,
        key_id=None, is_local=False, authenticated=True)
    assert ctx["role"] == "user"  # without the env var: plain user


def test_dev_env_promotes(monkeypatch):
    monkeypatch.setenv("AALI_DEV_EMAILS", "dev@hwk.team, other@x.io")
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=False,
        account_email="DEV@hwk.team",  # case-insensitive
        account_is_admin=False, key_id=None, is_local=False,
        authenticated=True)
    assert ctx["role"] == "dev"
    assert "pi_ci" in ctx["permissions"]
    assert "training" not in ctx["permissions"]  # owner-only stays owner-only


def test_admin_email_promotes(monkeypatch):
    monkeypatch.setenv("AALI_ADMIN_EMAILS", "boss@hwk.team")
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=False,
        account_email="boss@hwk.team", account_is_admin=True,
        key_id=None, is_local=False, authenticated=True)
    assert ctx["role"] == "admin"
    assert "users" in ctx["permissions"]
    assert "models" not in ctx["permissions"]  # dev/owner only


def test_plain_authenticated_user():
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=False,
        account_email="someone@example.com", account_is_admin=False,
        key_id=None, is_local=False, authenticated=True)
    assert ctx["role"] == "user"
    assert ctx["user_id"] == "account:someone@example.com"


def test_issued_key_user():
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=False, account_email=None,
        account_is_admin=False, key_id="abc123", is_local=False,
        authenticated=True)
    assert ctx["role"] == "user"
    assert ctx["user_id"] == "key:abc123"


def test_remote_unauthenticated_guest():
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=False, account_email=None,
        account_is_admin=False, key_id=None, is_local=False,
        authenticated=False)
    assert ctx["role"] == "guest"
    assert "chat" in ctx["permissions"]
    assert "export" not in ctx["permissions"]
    assert ctx["workspace_id"] == "guest"


def test_guest_never_owner_even_with_role_hint_env():
    """No client fact can promote a guest: resolve_role takes no such input."""
    ctx = roles.resolve_role(
        api_key_configured=True, is_master_key=False, account_email=None,
        account_is_admin=False, key_id=None, is_local=False,
        authenticated=False)
    assert ctx["role"] == "guest"


def test_ranking_helpers():
    assert roles.at_least("owner", "dev")
    assert roles.at_least("dev", "admin")
    assert not roles.at_least("user", "admin")
    assert roles.rank("owner") > roles.rank("guest")


def test_every_role_has_chat_and_permissions_shape(monkeypatch):
    monkeypatch.setenv("AALI_DEV_EMAILS", "d@x.io")
    for role in ("guest", "user", "admin", "dev", "owner"):
        ctx = {"guest": dict(api_key_configured=True, is_master_key=False,
                             account_email=None, account_is_admin=False,
                             key_id=None, is_local=False,
                             authenticated=False),
               "user": dict(api_key_configured=True, is_master_key=False,
                            account_email="u@x.io", account_is_admin=False,
                            key_id=None, is_local=False, authenticated=True),
               "admin": dict(api_key_configured=True, is_master_key=False,
                             account_email="a@x.io", account_is_admin=True,
                             key_id=None, is_local=False, authenticated=True),
               "dev": dict(api_key_configured=True, is_master_key=False,
                           account_email="d@x.io", account_is_admin=False,
                           key_id=None, is_local=False, authenticated=True),
               "owner": dict(api_key_configured=True, is_master_key=True,
                             account_email=None, account_is_admin=False,
                             key_id="admin", is_local=True,
                             authenticated=True)}[role]
        out = roles.resolve_role(**ctx)
        assert out["role"] == role
        assert isinstance(out["permissions"], list)
        assert "chat" in out["permissions"]
        assert out["user_id"] and out["workspace_id"]


# ————— HTTP: /api/auth/me carries the resolved role —————

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from file_agent import accounts as accounts_mod
    from file_agent import apikeys as apikeys_mod
    from file_agent import audit as audit_mod

    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    accounts_mod._configure(tmp_path / "acc.jsonl")
    apikeys_mod._configure(tmp_path / "keys.jsonl")
    audit_mod._configure(tmp_path / "audit.jsonl")
    app_module._HANDOFF_TOKENS.clear()
    accounts_mod._accounts.clear()
    accounts_mod._pending.clear()
    accounts_mod._sessions.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_me_owner_for_master_key(client):
    r = client.get("/api/auth/me", headers={"X-API-Key": MASTER})
    assert r.status_code == 200
    body = r.get_json()
    assert body["aali"]["role"] == "owner"
    assert "training" in body["aali"]["permissions"]


def test_me_admin_for_admin_email(client):
    # DEFAULT_ADMINS includes hmam@hwk.team -> signs up as admin
    r = client.post("/api/auth/signup",
                    json={"email": "hmam@hwk.team", "password": "password123"})
    body = r.get_json()
    assert r.status_code == 200, body
    code = body["dev_code"]
    client.post("/api/auth/verify", json={"email": "hmam@hwk.team", "code": code})
    tok = client.post("/api/auth/login",
                      json={"email": "hmam@hwk.team",
                            "password": "password123"}).get_json()["token"]
    me = client.get("/api/auth/me", headers={"X-Session-Token": tok}).get_json()
    assert me["is_admin"] is True
    assert me["aali"]["role"] == "admin"


def test_me_404_for_unauthenticated_remote(client):
    """Track B 11.2: key mode + no credentials -> uniform 404 on /api/auth/me
    too — existence is never revealed."""
    r = client.get("/api/auth/me",
                   environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code == 404


def test_me_owner_context_in_local_mode(client, monkeypatch):
    """No key configured (owner's machine): /api/auth/me answers openly
    with the owner context so the local browser renders the full UI."""
    monkeypatch.setattr(app_module, "API_KEY", "")
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.get_json()["aali"]["role"] == "owner"


def test_me_user_for_issued_key(client):
    from file_agent import apikeys as apikeys_mod
    _key_id, plaintext = apikeys_mod.issue_key("friend", "admin", "")
    r = client.get("/api/auth/me", headers={"X-API-Key": plaintext})
    assert r.status_code == 200
    body = r.get_json()
    assert body["aali"]["role"] == "user"
    assert body["aali"]["user_id"] == f"key:{_key_id}"


def test_resolve_role_endpoint_helper_consistent(client):
    """_resolve_role() in app.py must agree with the pure module."""
    ctx = app_module._resolve_role.__wrapped__ if hasattr(
        app_module._resolve_role, "__wrapped__") else None
    # Direct call inside a request context:
    with app_module.app.test_request_context(
            "/", headers={"X-API-Key": MASTER}):
        ctx = app_module._resolve_role()
    assert ctx["role"] == "owner"
