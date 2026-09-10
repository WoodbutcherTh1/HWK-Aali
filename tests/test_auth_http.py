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
from file_agent import audit  # noqa: E402

MASTER = "test-master-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Patch the module global (NOT os.environ): API_KEY is read at call time,
    # and env vars would leak into every other test module in the suite.
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    accounts._configure(tmp_path / "acc.jsonl")
    apikeys._configure(tmp_path / "keys.jsonl")
    audit._configure(tmp_path / "audit.jsonl")
    app_module._HANDOFF_TOKENS.clear()
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


def test_dashboard_token_handoff_dual_headers(client):
    """The /admin page saves the web app's one-click handoff (?token=<session>)
    and sends it in BOTH X-API-Key and X-Session-Token (the page cannot tell
    which kind of token it holds). An admin session token must pass through
    that combo; a user session token must stay blocked."""
    _signup_and_verify(client, "hmam@hwk.team")
    _signup_and_verify(client, "plain@example.com")
    admin_tok = _login(client, "hmam@hwk.team")
    user_tok = _login(client, "plain@example.com")

    def dashboard_headers(tok):
        return {"X-API-Key": tok, "X-Session-Token": tok}

    r = client.get("/api/admin/stats", headers=dashboard_headers(admin_tok))
    assert r.status_code == 200, r.get_json()

    r = client.get("/api/admin/stats", headers=dashboard_headers(user_tok))
    assert r.status_code == 401


def test_session_token_never_valid_as_api_key(client):
    """A session token pasted alone into the X-API-Key field must not open
    the admin surface — sessions authenticate only via X-Session-Token."""
    _signup_and_verify(client, "hmam@hwk.team")
    token = _login(client, "hmam@hwk.team")
    r = client.get("/api/admin/stats", headers={"X-API-Key": token})
    assert r.status_code == 401


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


def test_audit_records_admin_actions(client):
    """Issuing a key, revoking it, and deleting an account all land in the
    audit log with the acting admin's identity — and the plaintext key never
    enters a record."""
    _signup_and_verify(client, "hmam@hwk.team")
    _signup_and_verify(client, "victim@example.com")
    token = _login(client, "hmam@hwk.team")
    hdrs = _auth_headers(token)

    issued = client.post("/api/admin/keys", json={"label": "web-client"}, headers=hdrs)
    assert issued.status_code == 200
    key_id = issued.get_json()["key_id"]
    assert client.delete(f"/api/admin/keys/{key_id}", headers=hdrs).status_code == 200
    assert client.delete(
        "/api/admin/accounts?email=victim@example.com", headers=hdrs
    ).status_code == 200

    r = client.get("/api/admin/audit", headers=hdrs)
    assert r.status_code == 200, r.get_json()
    events = r.get_json()["events"]
    actions = [e["action"] for e in events]
    assert actions[:3] == ["account_delete", "key_revoke", "key_issue"]  # newest first
    # The actor is the admin's email, never a token; plaintext keys stay out.
    assert all(e["actor"] == "hmam@hwk.team" for e in events)
    assert all(issued.get_json()["api_key"] not in json.dumps(e) for e in events)


def test_audit_endpoint_admin_gated(client):
    """The audit trail is admin-only, like every /api/admin/* surface."""
    _signup_and_verify(client, "hmam@hwk.team")
    _signup_and_verify(client, "plain@example.com")
    admin_tok = _login(client, "hmam@hwk.team")
    user_tok = _login(client, "plain@example.com")

    assert client.get("/api/admin/audit", headers=_auth_headers(admin_tok)).status_code == 200
    assert client.get("/api/admin/audit", headers=_auth_headers(user_tok)).status_code == 401
    assert client.get("/api/admin/audit").status_code == 401
    assert client.get("/api/admin/audit", headers={"X-API-Key": MASTER}).status_code == 200


def test_audit_action_filter_and_limit(client):
    """?action= filters and ?limit= caps the trail."""
    hdrs = {"X-API-Key": MASTER}
    ids = [
        client.post("/api/admin/keys", json={"label": f"k{i}"}, headers=hdrs).get_json()["key_id"]
        for i in range(3)
    ]
    client.delete(f"/api/admin/keys/{ids[0]}", headers=hdrs)

    r = client.get("/api/admin/audit?action=key_revoke", headers=hdrs)
    events = r.get_json()["events"]
    assert len(events) == 1 and events[0]["action"] == "key_revoke"

    r = client.get("/api/admin/audit?limit=2", headers=hdrs)
    assert len(r.get_json()["events"]) == 2


def test_handoff_mint_requires_admin_session(client):
    """Only an authenticated admin session can mint a handoff token."""
    _signup_and_verify(client, "hmam@hwk.team")
    _signup_and_verify(client, "plain@example.com")
    admin_tok = _login(client, "hmam@hwk.team")
    user_tok = _login(client, "plain@example.com")

    assert client.post("/api/auth/handoff").status_code == 401
    assert client.post("/api/auth/handoff", headers=_auth_headers(user_tok)).status_code == 401
    assert client.post("/api/auth/handoff", headers={"X-API-Key": MASTER}).status_code == 401

    r = client.post("/api/auth/handoff", headers=_auth_headers(admin_tok))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] and body["handoff_token"] and body["ttl"] > 0


def test_handoff_redeem_single_use(client):
    """Redeem swaps ?ht= for a real admin session exactly once — reuse, a
    forged token, and a user-minted token all fail; the minted session must
    actually open the admin surface."""
    _signup_and_verify(client, "hmam@hwk.team")
    _signup_and_verify(client, "plain@example.com")
    admin_tok = _login(client, "hmam@hwk.team")

    r = client.post("/api/auth/handoff", headers=_auth_headers(admin_tok))
    ht = r.get_json()["handoff_token"]

    first = client.post("/api/admin/handoff-redeem", json={"handoff_token": ht})
    assert first.status_code == 200, first.get_json()
    body = first.get_json()
    assert body["ok"] and body["email"] == "hmam@hwk.team"
    # The minted session really is an admin session.
    me = client.get("/api/auth/me", headers=_auth_headers(body["token"]))
    assert me.status_code == 200 and me.get_json()["is_admin"]
    assert client.get(
        "/api/admin/stats", headers=_auth_headers(body["token"])
    ).status_code == 200

    # Single-use: the second redeem with the same token fails.
    second = client.post("/api/admin/handoff-redeem", json={"handoff_token": ht})
    assert second.status_code == 401

    # Forged garbage and a user's session token are not redeemable.
    assert client.post("/api/admin/handoff-redeem", json={"handoff_token": "nope"}).status_code == 401
    assert client.post(
        "/api/admin/handoff-redeem", json={"handoff_token": admin_tok}
    ).status_code == 401


def test_handoff_redeem_expired(client):
    """A token past its 60s TTL is rejected (already consumed from the store)."""
    _signup_and_verify(client, "hmam@hwk.team")
    tok = _login(client, "hmam@hwk.team")
    r = client.post("/api/auth/handoff", headers=_auth_headers(tok))
    ht = r.get_json()["handoff_token"]
    # Expire every pending handoff token.
    for v in app_module._HANDOFF_TOKENS.values():
        v["expires"] = 0.0
    expired = client.post("/api/admin/handoff-redeem", json={"handoff_token": ht})
    assert expired.status_code == 401


def test_handoff_audit_events(client):
    """Mint and redeem land in the audit trail."""
    _signup_and_verify(client, "hmam@hwk.team")
    tok = _login(client, "hmam@hwk.team")
    ht = client.post("/api/auth/handoff", headers=_auth_headers(tok)).get_json()["handoff_token"]
    client.post("/api/admin/handoff-redeem", json={"handoff_token": ht})

    events = client.get("/api/admin/audit", headers=_auth_headers(tok)).get_json()["events"]
    actions = [e["action"] for e in events]
    assert "handoff_mint" in actions and "handoff_redeem" in actions
    mint = next(e for e in events if e["action"] == "handoff_mint")
    assert mint["actor"] == "hmam@hwk.team" and mint["target"] == "hmam@hwk.team"
    # The raw handoff token and the session token never enter the log.
    assert ht not in json.dumps(events)


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
