"""Wave 3 #8+#9: shared conversations + prompt library (2026-09-25).

Shares contract: token IS the credential (stored hashed, shown once),
read-only frozen copy, expiry/revoke = instant 404 everywhere, list is
content-free, ns-scoped revoke, uniform-404 page for unknown tokens.
Prompts contract: builtins always present, custom prompts ns-scoped with
cap, insert-only from the UI, uniform-404 for guests.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from file_agent import accounts  # noqa: E402
from file_agent import apikeys  # noqa: E402
from file_agent import audit  # noqa: E402
from file_agent import prompts as pr  # noqa: E402
from file_agent import shares as sh  # noqa: E402

MASTER = "test-master-key"

TURNS = [
    {"role": "user", "content": "مرحبا يا آلي", "ts": time.time()},
    {"role": "assistant", "content": "أهلاً بك! كيف أساعدك؟", "ts": time.time()},
]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    sp = tmp_path / "shares.jsonl"
    pp = tmp_path / "prompts.jsonl"
    monkeypatch.setattr(sh, "DEFAULT_STORE", sp)
    monkeypatch.setattr(pr, "DEFAULT_STORE", pp)
    return {"shares": sp, "prompts": pp}


@pytest.fixture()
def client(tmp_path, monkeypatch, store):
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    accounts._configure(tmp_path / "acc.jsonl")
    apikeys._configure(tmp_path / "keys.jsonl")
    audit._configure(tmp_path / "audit.jsonl")
    accounts._accounts.clear()
    accounts._pending.clear()
    accounts._sessions.clear()
    app_module._sessions.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _h(key=MASTER):
    return {"X-API-Key": key}


def _plant(sid="s1", turns=None):
    """Plant a session under the key format the ask/share paths use.
    NOTE: computed WITHOUT _user_ns() — that helper reads request headers
    and would explode outside a request context. With API_KEY set and a
    master-key caller, the key format is u<key>:<sid> (see _user_ns)."""
    import time as _t
    key = f"u{MASTER}:{sid}" if app_module.API_KEY else sid
    app_module._sessions[key] = {
        "key": key, "sid": sid, "turns": turns or TURNS,
        "created_at": _t.time(), "updated_at": _t.time()}
    return app_module._sessions[key]


# ————————————————————————————— shares module —————————————————————————————

def test_create_returns_token_once_and_stores_hash(store):
    out = sh.create_share("s1", TURNS, ns="", path=store["shares"])
    assert out["token"] and out["turns"] == 2
    recs = sh._load(store["shares"])
    assert list(recs.values())[0]["token_hash"].startswith("sha:")
    assert out["token"] not in sh._load(store["shares"])


def test_resolve_bumps_views_and_revoke_kills(store):
    out = sh.create_share("s1", TURNS, ns="", path=store["shares"])
    rec = sh.resolve_share(out["token"], path=store["shares"])
    assert rec and rec["views"] == 1
    assert sh.resolve_share(out["token"], path=store["shares"])["views"] == 2
    h = list(sh._load(store["shares"]).keys())[0]
    assert sh.revoke(h, ns="", path=store["shares"]) is True
    assert sh.resolve_share(out["token"], path=store["shares"]) is None


def test_expiry_is_404_everywhere(store):
    out = sh.create_share("s1", TURNS, ttl_hours=0.1, ns="",
                          path=store["shares"])
    recs = sh._load(store["shares"])
    for r in recs.values():
        r["expires"] = time.time() - 1
    sh._save(recs, store["shares"])
    assert sh.resolve_share(out["token"], path=store["shares"]) is None


def test_ns_scoped_list_and_revoke(store):
    a = sh.create_share("a", TURNS, ns="u1", path=store["shares"])
    b = sh.create_share("b", TURNS, ns="u2", path=store["shares"])
    mine = sh.list_shares("u1", path=store["shares"])
    assert len(mine) == 1 and mine[0]["sid"] == "a"
    h2 = sh.list_shares("u2", path=store["shares"])[0]["token_hash"]
    assert sh.revoke(h2, ns="u1", path=store["shares"]) is False  # not mine
    assert sh.revoke(h2, ns="u2", path=store["shares"]) is True
    assert sh.resolve_share(a["token"], path=store["shares"]) is not None
    assert sh.resolve_share(b["token"], path=store["shares"]) is None


def test_empty_and_garbage_tokens(store):
    assert sh.resolve_share("", path=store["shares"]) is None
    assert sh.resolve_share("x" * 5000, path=store["shares"]) is None
    assert sh.resolve_share("nope", path=store["shares"]) is None


# ————————————————————————————— shares endpoints —————————————————————————————

def test_share_endpoints_roundtrip(client, store):
    _plant("sess-share")
    r = client.post("/api/shares", headers=_h(),
                    json={"sid": "sess-share", "title": "تجربة"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["url"].startswith("http") and "/share/" in body["url"]
    listing = client.get("/api/shares", headers=_h()).get_json()["shares"]
    assert len(listing) == 1 and listing[0]["views"] == 0
    assert "token" not in listing[0] and "turns" not in listing[0]
    # public view works
    page = client.get("/share/" + body["token"])
    assert page.status_code == 200 and "أهلاً بك" in page.get_data(as_text=True)
    # revoke by hash -> page 404
    th = listing[0]["token_hash"]
    assert client.delete(f"/api/shares/{th}", headers=_h()).get_json()["ok"]
    page = client.get("/share/" + body["token"])
    assert page.status_code == 404


def test_share_page_404_for_unknown_and_garbage(client):
    assert client.get("/share/definitely-not-a-token").status_code == 404
    assert client.get("/share/").status_code == 404


def test_share_unknown_session_404(client, store):
    assert client.post("/api/shares", headers=_h(),
                       json={"sid": "ghost"}).status_code == 404


def test_share_endpoints_guest_404(client, store):
    r = client.post("/api/shares", json={"sid": "x"},
                    headers={"X-Forwarded-For": "9.9.9.9"},
                    environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 404


def test_share_page_escapes_html(client, store):
    evil = [{"role": "user", "content": "<script>alert(1)</script>",
             "ts": time.time()}]
    _plant("sess-evil", evil)
    body = client.post("/api/shares", headers=_h(),
                       json={"sid": "sess-evil"}).get_json()
    page = client.get("/share/" + body["token"]).get_data(as_text=True)
    assert "<script>" not in page and "&lt;script&gt;" in page


# ————————————————————————————— prompts —————————————————————————————

def test_prompts_builtins_present(store):
    data = pr.list_prompts("", path=store["prompts"])
    assert len(data["builtin"]) >= 10
    assert data["custom"] == []
    assert any(p["lang"] == "ar" for p in data["builtin"])
    assert any(p["lang"] == "en" for p in data["builtin"])


def test_prompts_add_list_delete_ns_scoped(store):
    assert pr.add_prompt("عنوان", "النص", ns="u1",
                         path=store["prompts"])["ok"]
    assert pr.add_prompt("other", "body", ns="u2",
                         path=store["prompts"])["ok"]
    mine = pr.list_prompts("u1", path=store["prompts"])
    assert len(mine["custom"]) == 1 and mine["custom"][0]["title"] == "عنوان"
    pid = mine["custom"][0]["id"]
    assert pr.delete_prompt(pid, ns="u2", path=store["prompts"])["ok"] is False
    assert pr.delete_prompt(pid, ns="u1", path=store["prompts"])["ok"] is True
    assert pr.list_prompts("u1", path=store["prompts"])["custom"] == []


def test_prompts_empty_rejected_and_cap(store):
    assert pr.add_prompt("", "body", ns="", path=store["prompts"])["ok"] is False
    assert pr.add_prompt("t", "  ", ns="", path=store["prompts"])["ok"] is False
    for i in range(pr.MAX_CUSTOM_PER_NS):
        assert pr.add_prompt(f"t{i}", "b", ns="cap", path=store["prompts"])["ok"]
    assert pr.add_prompt("over", "b", ns="cap",
                         path=store["prompts"])["ok"] is False


# ————————————————————————————— prompts endpoints —————————————————————————————

def test_prompts_endpoints_roundtrip(client, store):
    r = client.get("/api/prompts", headers=_h())
    assert r.status_code == 200 and r.get_json()["builtin"]
    r = client.post("/api/prompts", headers=_h(),
                    json={"title": "موجهي", "body": "نص الموجه"})
    assert r.status_code == 201
    pid = r.get_json()["prompt"]["id"]
    data = client.get("/api/prompts", headers=_h()).get_json()
    assert [c["id"] for c in data["custom"]] == [pid]
    assert client.delete(f"/api/prompts/{pid}", headers=_h()).get_json()["ok"]
    assert client.get("/api/prompts", headers=_h()).get_json()["custom"] == []


def test_prompts_guest_404_and_bad_add_400(client, store):
    r = client.get("/api/prompts",
                   headers={"X-Forwarded-For": "9.9.9.9"},
                   environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 404
    assert client.post("/api/prompts", headers=_h(),
                       json={"title": "", "body": ""}).status_code == 400
    assert client.delete("/api/prompts/p_nope",
                         headers=_h()).status_code == 404
