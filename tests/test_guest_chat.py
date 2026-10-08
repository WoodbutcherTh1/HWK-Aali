"""Guest chat tier (2026-10-08) — anonymous visitors may TALK.

Contract (DeepSeek/Gemini-style, owner request):
- No account required to chat: /api/ask + /api/ask/stream answer for an
  anonymous remote caller, rate-capped and forced into the server-enforced
  guest policy (chat, never commands).
- Per-browser isolation: the hwk_guest cookie scopes sessions into their own
  namespace — one guest can never read another guest's conversation.
- The 404-not-revealing contract is UNCHANGED for every other surface
  (projects, attachments, search, TTS...) — the whitelist is closed.
- Local loopback callers (the owner's browser) chat uncapped.

Remote simulation follows the suite convention: X-Forwarded-For + a
non-loopback REMOTE_ADDR (the tunnel sets XFF, so tunnel guests are gated
exactly like this).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402

MASTER = "test-master-key"
_HEADERS = {"X-Forwarded-For": "9.9.9.9"}
_BASE = {"REMOTE_ADDR": "203.0.113.5"}


def _remote(**kw):
    """Simulate a REMOTE (tunnel) visitor the way the suite does."""
    return {"headers": _HEADERS, "environ_base": _BASE, **kw}


@pytest.fixture()
def guest_env(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    app_module._sessions.clear()
    app_module._GUEST_RATE.clear()
    yield app_module


def _new_client():
    """A FRESH browser: its own cookie jar == its own guest identity."""
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _fake_loop(monkeypatch, seen):
    def fake_loop(message, *a, **kw):
        seen["message"] = message
        seen["kw"] = kw
        return "رد آلي"
    monkeypatch.setattr(app_module, "agent_loop", fake_loop)
    return fake_loop


# ---------------------------------------------------------------- chat works
def test_guest_can_chat_without_credentials(guest_env, monkeypatch):
    seen: dict = {}
    _fake_loop(monkeypatch, seen)
    c = _new_client()
    r = c.post("/api/ask", json={"message": "مرحبا", "sid": "gs1"}, **_remote())
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True and body["reply"] == "رد آلي"
    # a guest namespace is in use (the follow-up request reuses the minted cookie)
    r2 = c.post("/api/ask", json={"message": "زيد", "sid": "gs1"}, **_remote())
    assert r2.status_code == 200
    assert any(k.startswith("g") and ":gs1" in k for k in app_module._sessions), \
        "guest ns in use"


def test_guest_session_roundtrip_and_isolation(guest_env, monkeypatch):
    _fake_loop(monkeypatch, {})
    a = _new_client()  # guest A
    b = _new_client()  # guest B — different browser
    a.post("/api/ask", json={"message": "سري لضيفي", "sid": "shared-sid"}, **_remote())
    # A can read their own conversation
    ra = a.get("/api/session/shared-sid", **_remote())
    assert ra.status_code == 200
    # B must not see A's conversation — not by sid, not via the sidebar
    rb = b.get("/api/session/shared-sid", **_remote())
    assert rb.status_code == 404
    lst = b.get("/api/sessions", **_remote())
    sids = [row["sid"] for row in lst.get_json().get("sessions", [])]
    assert "shared-sid" not in sids
    # ...and cannot delete it either
    assert b.delete("/api/session/shared-sid", **_remote()).status_code == 404


def test_guest_sees_own_sessions_list(guest_env, monkeypatch):
    _fake_loop(monkeypatch, {})
    c = _new_client()
    c.post("/api/ask", json={"message": "محادثتي الأولى", "sid": "mine"}, **_remote())
    lst = c.get("/api/sessions", **_remote())
    assert lst.status_code == 200
    sids = [row["sid"] for row in lst.get_json()["sessions"]]
    assert "mine" in sids


# ---------------------------------------------------------------- rate cap
def test_guest_burst_cap_fires_with_arabic_upsell(guest_env, monkeypatch):
    _fake_loop(monkeypatch, {})
    monkeypatch.setitem(app_module._GUEST_BURST, "max", 2)
    c = _new_client()
    for _ in range(2):
        r = c.post("/api/ask", json={"message": "سؤال", "sid": "cap"}, **_remote())
        assert r.status_code == 200
    r3 = c.post("/api/ask", json={"message": "سؤال", "sid": "cap"}, **_remote())
    assert r3.status_code == 429
    err = r3.get_json()["error"]
    assert "حساب" in err and "الزائر" in err  # honest upsell, not a bare number


def test_local_anonymous_is_never_capped(guest_env, monkeypatch):
    seen: dict = {}
    _fake_loop(monkeypatch, seen)
    # exhaust the day bucket for a guest id, then ask from LOOPBACK:
    app_module._GUEST_RATE["f" * 32] = [app_module.time.time()] * 999
    c = _new_client()
    c.set_cookie("hwk_guest", "f" * 32)
    r = c.post("/api/ask", json={"message": "أنا المالك محلياً", "sid": "own"})
    assert r.status_code == 200, r.get_json()


def test_local_anonymous_keeps_full_policy(guest_env, monkeypatch):
    seen: dict = {}
    _fake_loop(monkeypatch, seen)
    c = _new_client()
    c.post("/api/ask", json={"message": "شغل أمر", "sid": "own2", "policy": "auto"})
    assert seen["kw"]["policy"] == "auto"


# ---------------------------------------------------------------- policy fence
def test_remote_guest_forced_guest_policy(guest_env, monkeypatch):
    seen: dict = {}
    _fake_loop(monkeypatch, seen)
    c = _new_client()
    r = c.post("/api/ask", json={"message": "نفّذ أمراً", "sid": "p1",
                                 "policy": "aggressive", "confirm": True}, **_remote())
    assert r.status_code == 200
    assert seen["kw"]["policy"] == "guest"        # server-enforced, never client
    assert seen["kw"].get("confirmed") is False   # confirm is not a boundary


# ---------------------------------------------------------------- 404 contract
@pytest.mark.parametrize("path,method", [
    ("/api/projects", "GET"),
    ("/api/attach", "POST"),
    ("/api/search?q=x", "GET"),
    ("/api/voice/synthesize", "POST"),
    ("/api/auth/me", "GET"),
])
def test_everything_else_stays_uniform_404(guest_env, path, method):
    c = _new_client()
    r = c.open(path, method=method, **_remote())
    assert r.status_code == 404, f"{path} leaked for a guest: {r.status_code}"


# ---------------------------------------------------------------- unit: ns
def test_user_ns_guest_branch_isolated_per_browser(guest_env, monkeypatch):
    from flask import g as flask_g
    with app_module.app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        app_module.app.preprocess_request()  # not needed; call helper directly
        flask_g.guest_id = "a" * 32
        ns_a = app_module._user_ns("s1")
        flask_g.guest_id = "b" * 32
        ns_b = app_module._user_ns("s1")
    assert ns_a.startswith("ga" * 16 + ":s1") or ns_a == f"g{'a' * 32}:s1"
    assert ns_b == f"g{'b' * 32}:s1"
    assert ns_a != ns_b  # two browsers, two namespaces, same sid string
