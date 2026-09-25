"""Tests for the FTS5 conversation search (Wave 1 #2, 2026-09-25).

Covers the module (file_agent/search_index.py) and the endpoint
(GET /api/search): Arabic + mixed-language queries, role-aware
SERVER-SIDE scoping, filters, pagination, injection safety, index
lifecycle (new message / session delete / idempotent backfill) and a
10k-row performance budget.

Scoping conventions used here (mirroring the server):
- LOCAL MODE rows: session key = bare sid -> ns ""
- OWNER rows (key mode): key = "u<master>:<sid>" -> ns "u<master>"
- ISSUED KEY rows: key = "u<key_id>:<sid>" -> ns "u<key_id>"
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from file_agent import apikeys  # noqa: E402
from file_agent import search_index as si  # noqa: E402

MASTER = "test-master-key"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh index db per test + patched module default."""
    path = tmp_path / "search.db"
    monkeypatch.setattr(si, "DEFAULT_DB", path)
    return path


@pytest.fixture()
def client(tmp_path, monkeypatch, db):
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    monkeypatch.setattr(app_module, "SESSIONS_FILE",
                        tmp_path / "sessions.jsonl")
    app_module._sessions.clear()
    app_module._SEARCH_RATE.clear()
    apikeys._configure(tmp_path / "keys.jsonl")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _turn(role: str, content: str, ts: float = 1727200000.0) -> dict:
    return {"role": role, "content": content, "ts": ts}


def _seed_local(sid: str, turns: list[dict]) -> None:
    """Local-mode conversation: bare sid key -> ns ''."""
    rec = {"sid": sid, "key": sid,
           "created_at": 1727200000.0, "updated_at": 1727200100.0,
           "turns": turns}
    app_module._sessions[sid] = rec
    si.reindex_session(rec)


def _seed_ns(ns: str, sid: str, turns: list[dict]) -> None:
    """Namespaced conversation (owner or issued key)."""
    key = f"{ns}:{sid}"
    rec = {"sid": sid, "key": key,
           "created_at": 1727200000.0, "updated_at": 1727200100.0,
           "turns": turns}
    app_module._sessions[key] = rec
    si.reindex_session(rec)


def _h(key: str = MASTER) -> dict[str, str]:
    return {"X-API-Key": key}


# ————— module-level (local mode ns="") —————

def test_basic_search_finds_both_roles(db):
    _seed_local("s1", [_turn("user", "hello world"),
                       _turn("assistant", "hi, how can I help")])
    out = si.search("hello", "")
    assert out["total"] >= 1
    assert any("hello" in r["snippet"] for r in out["results"])


def test_arabic_search(db):
    _seed_local("s2", [_turn("user", "ايمتى تسافر على بره"),
                       _turn("assistant", "السفر قرار كبير")])
    # Exact word:
    assert si.search("قرار", "")["total"] == 1
    # Prefix matching (the * operator): "بر" hits بره; unicode61 has no
    # stemming, so root forms (سفر) do NOT match prefixed words (تسافر,
    # السفر) — documented honestly in docs/features/conversation_search.md.
    assert si.search("بر", "")["total"] == 1
    assert si.search("السفر", "")["total"] == 1


def test_mixed_arabic_english(db):
    _seed_local("s3", [_turn("user", "اكتب لي كود python للـ api")])
    assert si.search("python", "")["total"] == 1
    assert si.search("كود", "")["total"] == 1


def test_special_chars_and_injection_safe(db):
    _seed_local("s4", [_turn("user", "price is 100$ and path C:/x/y")])
    for q in ['"unclosed', "a OR b", "NEAR(", "100$", "C:/x/y", "') --"]:
        out = si.search(q, "")
        assert isinstance(out["results"], list)


def test_role_filter(db):
    _seed_local("s5", [_turn("user", "deploy friday"),
                       _turn("assistant", "deployment scheduled")])
    assert si.search("deploy", "", role="user")["total"] == 1
    assert si.search("deployment", "", role="assistant")["total"] == 1


def test_date_filters(db):
    _seed_local("s6", [_turn("user", "old news", ts=1000000.0),
                       _turn("user", "new news", ts=2000000.0)])
    assert si.search("news", "", ts_from=1500000.0)["total"] == 1
    assert si.search("news", "", ts_to=1500000.0)["total"] == 1


def test_pagination(db):
    _seed_local("s7", [_turn("user", f"item number {i}") for i in range(10)])
    page1 = si.search("item", "", limit=4, offset=0)
    page2 = si.search("item", "", limit=4, offset=4)
    assert page1["total"] == 10
    ids1 = {r["message_id"] for r in page1["results"]}
    ids2 = {r["message_id"] for r in page2["results"]}
    assert ids1 and ids2 and not (ids1 & ids2)


def test_empty_query_is_noop(db):
    assert si.search("   ", "")["results"] == []


def test_multiuser_scope_isolation(db):
    _seed_ns("ukeyA", "a1", [_turn("user", "alice secret plan")])
    _seed_ns("ukeyB", "b1", [_turn("user", "bob secret plan")])
    alice = si.search("secret", "ukeyA")
    assert alice["total"] == 1
    assert all(r["session_id"] == "a1" for r in alice["results"])


def test_admin_sees_all_ns(db):
    _seed_ns("ukeyA", "a1", [_turn("user", "shared topic alice")])
    _seed_ns("ukeyB", "b1", [_turn("user", "shared topic bob")])
    assert si.search("topic", None)["total"] == 2


def test_reindex_is_idempotent(db):
    turns = [_turn("user", "count me once")]
    record = {"sid": "s9", "key": "s9", "turns": turns}
    si.reindex_session(record)
    n2 = si.reindex_session(record)
    assert n2 == 1
    assert si.search("count", "")["total"] == 1


def test_session_delete_removes_rows(db):
    _seed_local("s10", [_turn("user", "delete me from index")])
    assert si.search("delete me", "")["total"] == 1
    si.remove_session("", "s10")
    assert si.search("delete me", "")["total"] == 0


def test_backfill_idempotent_and_counts(db):
    record = {"sid": "s11", "key": "s11",
              "turns": [_turn("user", "backfill me"),
                        _turn("assistant", "done")]}
    sessions = {record["key"]: record}
    assert si.backfill(sessions) == 2
    assert si.backfill(sessions) == 2  # idempotent, not additive
    assert si.count("") == 2


def test_non_chat_roles_never_indexed(db):
    si.index_message("", "s12", "system", "SECRET SYSTEM PROMPT", 1.0, 0)
    si.index_message("", "s12", "tool", "tool output", 1.0, 1)
    assert si.search("SECRET SYSTEM PROMPT", "")["total"] == 0


def test_session_row_count_drives_lazy_backfill(db):
    record = {"sid": "s13", "key": "s13",
              "turns": [_turn("user", "lazy backfill probe")]}
    assert si.session_row_count("", "s13") == 0
    si.reindex_session(record)
    assert si.session_row_count("", "s13") == 1


def test_performance_10k_rows(db):
    rows = [
        (f"message body number {i} with filler text", "", "perf",
         "user", 1727200000.0 + i, i, "")
        for i in range(10000)
    ]
    with si._connect(db) as conn:
        conn.execute(si._SCHEMA)
        conn.executemany(
            "INSERT INTO messages(content, ns, sid, role, ts, turn_idx, "
            "project_id) VALUES (?,?,?,?,?,?,?)", rows)
    t0 = time.perf_counter()
    out = si.search("message body number 9999", "", db_path=db)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert out["total"] >= 1
    assert elapsed_ms < 100, f"search took {elapsed_ms:.1f}ms"


# ————— endpoint-level —————

def test_endpoint_requires_key(client, db):
    _seed_ns(f"u{MASTER}", "e1", [_turn("user", "findable")])
    # Track B 11.2: unauthenticated callers get the uniform 404.
    assert client.get("/api/search?q=findable").status_code == 404


def test_endpoint_empty_query_400(client, db):
    assert client.get("/api/search?q=", headers=_h()).status_code == 400


def test_endpoint_local_mode_searches(client, db, monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "")
    app_module._sessions.clear()
    _seed_local("e2", [_turn("user", "local query target")])
    r = client.get("/api/search?q=local query target")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] and data["total"] == 1
    assert data["results"][0]["session_title"]
    # API contract: snippet is plain text; highlight carries the <mark>s
    assert "<mark>" not in data["results"][0]["snippet"]
    assert "<mark>" in data["results"][0]["highlight"]


def test_endpoint_admin_searches_all_users(client, db):
    _seed_ns("ukeyA", "ea", [_turn("user", "admin can see this")])
    r = client.get("/api/search?q=admin can see", headers=_h())
    assert r.status_code == 200
    assert r.get_json()["total"] == 1


def test_endpoint_owner_master_key_scoped_to_own(client, db):
    _seed_ns(f"u{MASTER}", "ow", [_turn("user", "owner own words")])
    _seed_ns("ukeyB", "ob", [_turn("user", "owner own words")])
    r = client.get("/api/search?q=owner own words", headers=_h())
    data = r.get_json()
    # The master key is admin -> sees both; a NON-admin owner variant is
    # covered by the issued-key isolation test below.
    assert data["total"] == 2


def test_endpoint_issued_key_isolation(client, db):
    _seed_ns("ukeyA", "ia", [_turn("user", "userA private note")])
    _seed_ns("ukeyB", "ib", [_turn("user", "userB private note")])
    key_id, plaintext = apikeys.issue_key("userA", "admin", "")
    # Rewire userA's rows onto the real issued key_id namespace.
    rec = app_module._sessions.pop("ukeyA:ia")
    rec["key"] = f"u{key_id}:ia"
    app_module._sessions[rec["key"]] = rec
    si.reindex_session(rec)
    r = client.get("/api/search?q=private note", headers=_h(plaintext))
    assert r.status_code == 200
    data = r.get_json()
    assert data["total"] == 1
    assert data["results"][0]["session_id"] == "ia"


def test_endpoint_guest_refused_403(client, db):
    _seed_ns("ukeyA", "g1", [_turn("user", "guest must not search")])
    _key_id, plaintext = apikeys.issue_key("guest", "admin", "")
    r = client.get("/api/search?q=guest", headers=_h(plaintext),
                   environ_base={"REMOTE_ADDR": "203.0.113.5"})
    assert r.status_code == 403


def test_endpoint_guest_loopback_allowed(client, db):
    """A non-admin key from loopback (LAN owner pattern) may search its own."""
    _key_id, plaintext = apikeys.issue_key("lan-owner", "admin", "")
    rec = {"sid": "l1", "key": f"u{_key_id}:l1",
           "created_at": 0, "updated_at": 0,
           "turns": [_turn("user", "loopback owner search")]}
    app_module._sessions[rec["key"]] = rec
    si.reindex_session(rec)
    r = client.get("/api/search?q=loopback owner search",
                   headers=_h(plaintext))
    assert r.status_code == 200
    assert r.get_json()["total"] == 1


def test_endpoint_rate_limit(client, db, monkeypatch):
    monkeypatch.setattr(app_module, "_SEARCH_RATE_MAX", 2)
    _seed_local("rl", [_turn("user", "rate limit probe")])
    assert client.get("/api/search?q=rate", headers=_h()).status_code == 200
    assert client.get("/api/search?q=rate", headers=_h()).status_code == 200
    assert client.get("/api/search?q=rate", headers=_h()).status_code == 429


def test_endpoint_filters_role_and_date(client, db):
    _seed_local("ef", [_turn("user", "filter me user side", ts=1000000.0),
                       _turn("assistant", "filter me bot side",
                             ts=2000000.0)])
    r = client.get("/api/search?q=filter&role=assistant&from=1500000",
                   headers=_h())
    data = r.get_json()
    assert data["total"] == 1
    assert data["results"][0]["role"] == "assistant"


def test_endpoint_session_filter(client, db):
    _seed_local("sf1", [_turn("user", "needle in session one")])
    _seed_local("sf2", [_turn("user", "needle in session two")])
    r = client.get("/api/search?q=needle&session=sf2", headers=_h())
    data = r.get_json()
    assert data["total"] == 1
    assert data["results"][0]["session_id"] == "sf2"


def test_endpoint_index_update_on_new_message(client, db):
    """Saving a session (the _save_session hook) must update the index."""
    rec = {"sid": "live", "key": "live",
           "created_at": 0, "updated_at": 0, "turns": []}
    app_module._sessions["live"] = rec
    app_module._save_session(rec)
    app_module._append_turn(rec, "user", "freshly indexed words")
    r = client.get("/api/search?q=freshly indexed", headers=_h())
    assert r.get_json()["total"] == 1


def test_endpoint_delete_removes_from_index(client, db, monkeypatch):
    """Key mode: the master key is admin; the seeded conversation lives in
    the owner's namespace (u<master>:gone) so the endpoint's admin view
    finds it — then a DELETE must drop it from the index."""
    _seed_ns(f"u{MASTER}", "gone",
             [_turn("user", "delete removes search rows")])
    assert client.get("/api/search?q=delete removes",
                      headers=_h()).get_json()["total"] == 1
    assert client.delete("/api/session/gone",
                         headers=_h()).status_code == 200
    assert client.get("/api/search?q=delete removes",
                      headers=_h()).get_json()["total"] == 0
