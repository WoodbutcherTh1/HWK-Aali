"""Wave 4: custom assistants (2026-09-25).

Contract under test:
- Store: create/get/list/update/delete, ns ownership (one user's
  assistants invisible to another), Arabic-first errors, caps
  (empty name, per-ns limit, field trimming), pin/unpin project.
- persona_block: labeled Arabic text, empty string when no assistant
  (honest empty, never a fake persona).
- Endpoints: role gate (guest -> uniform 404, user+ -> allowed),
  CRUD round-trip, PUT unpin semantics (absent leaves, false/""
  unpins, string pins), session bind + /api/assistants/active,
  unknown session/assistant -> 404.
- Ask-time injection: the persona line LEADS the message (injected
  AFTER the project-instruction prepend), absent without a binding.
- Detach-on-delete: deleting an assistant clears every session bind;
  a bind whose assistant vanished elsewhere detaches silently at ask.
- SECURITY: an assistant is TEXT ONLY — binding one never alters the
  tool policy the caller asked for (guest policy is server-enforced
  upstream; here we pin that the requested policy reaches the loop
  unchanged with a persona active).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "file-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from file_agent import accounts  # noqa: E402
from file_agent import apikeys  # noqa: E402
from file_agent import audit  # noqa: E402
from file_agent import assistants as ast  # noqa: E402
from file_agent import projects as prj  # noqa: E402

MASTER = "test-master-key"


def _sess_key(sid):
    """Replicate the _user_ns key format OUTSIDE a request context (the
    documented lesson: _user_ns is request-scoped; tests must never call
    it outside one). Master-key callers carry is_admin=True, so _user_ns
    embeds the PLAINTEXT key via _client_key(): "u<key>:<sid>"."""
    return f"u{MASTER}:{sid}"


@pytest.fixture()
def store(tmp_path):
    return tmp_path / "assistants.jsonl"


@pytest.fixture()
def client(tmp_path, monkeypatch, store):
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    monkeypatch.setattr(ast, "DEFAULT_STORE", store)
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


def _plant(sid):
    """Plant a session the ask path will accept (FRESH timestamps —
    _get_session prunes stale records, which silently drops the bind)."""
    import time as _t
    key = _sess_key(sid)
    app_module._sessions[key] = {
        "key": key, "sid": sid, "turns": [],
        "created_at": _t.time(), "updated_at": _t.time()}
    return app_module._sessions[key]


# ————————————————————————————— store —————————————————————————————

def test_create_get_roundtrip(store):
    out = ast.create_assistant("مدرّس الرياضيات", "", icon="🧑‍🏫",
                               tagline="يشرح خطوة بخطوة",
                               instruction="اشرح ببطء وبالعربية",
                               path=store)
    assert out["ok"] is True
    aid = out["assistant"]["id"]
    assert aid.startswith("as_")
    a = ast.get_assistant(aid, "", path=store)
    assert a is not None
    assert a["name"] == "مدرّس الرياضيات"
    assert a["icon"] == "🧑‍🏫"
    assert a["tagline"] == "يشرح خطوة بخطوة"
    assert a["instruction"] == "اشرح ببطء وبالعربية"


def test_create_empty_name_arabic_error(store):
    out = ast.create_assistant("   ", "", path=store)
    assert out["ok"] is False and "المساعد" in out["error"]


def test_ns_isolation(store):
    aid = ast.create_assistant("لي", "u1", path=store)["assistant"]["id"]
    assert ast.get_assistant(aid, "u2", path=store) is None
    assert ast.get_assistant(aid, "u1", path=store) is not None
    assert ast.list_assistants("u2", path=store) == []
    assert ast.delete_assistant(aid, "u2", path=store)["ok"] is False
    assert ast.delete_assistant(aid, "u1", path=store)["ok"] is True
    assert ast.list_assistants("u1", path=store) == []


def test_list_newest_first(store):
    ids = [ast.create_assistant(f"م{i}", "", path=store)["assistant"]["id"]
           for i in range(3)]
    assert [a["id"] for a in ast.list_assistants("", path=store)] == \
        list(reversed(ids))


def test_caps_and_trimming(store):
    long_name = "ن" * 200
    out = ast.create_assistant(long_name, "", tagline="ه" * 500,
                               instruction="ت" * 5000, path=store)
    a = out["assistant"]
    assert len(a["name"]) == ast.MAX_NAME
    assert len(a["tagline"]) == ast.MAX_TAGLINE
    assert len(a["instruction"]) == ast.MAX_INSTRUCTION


def test_per_ns_cap(store):
    for i in range(ast.MAX_PER_NS):
        assert ast.create_assistant(f"م{i}", "cap", path=store)["ok"]
    assert ast.create_assistant("فائض", "cap", path=store)["ok"] is False
    assert "الحد" in ast.create_assistant("فائض", "cap", path=store)["error"]
    # Other namespaces are unaffected.
    assert ast.create_assistant("مسموح", "other", path=store)["ok"] is True


def test_update_partial_and_unpin(store):
    aid = ast.create_assistant("قبل", "", tagline="قديم",
                               path=store)["assistant"]["id"]
    out = ast.update_assistant(aid, "", tagline="جديد", path=store)
    a = out["assistant"]
    assert a["tagline"] == "جديد" and a["name"] == "قبل"
    # project_id: str pins, False unpins, None leaves.
    assert ast.update_assistant(aid, "", project_id="proj_1",
                                path=store)["assistant"]["project_id"] \
        == "proj_1"
    assert ast.update_assistant(aid, "", tagline="لمس",
                                path=store)["assistant"]["project_id"] \
        == "proj_1"
    assert ast.update_assistant(aid, "", project_id=False,
                                path=store)["assistant"]["project_id"] is None
    # Unknown id / empty name keep the Arabic-error contract.
    assert ast.update_assistant("as_nope", "", name="x",
                                path=store)["ok"] is False
    assert ast.update_assistant(aid, "", name="   ",
                                path=store)["ok"] is False


def test_delete_unknown(store):
    assert ast.delete_assistant("as_nope", "", path=store)["ok"] is False


def test_persona_block(store):
    aid = ast.create_assistant("سالم", "", tagline="مختصر",
                               instruction="أجب بالعربية",
                               path=store)["assistant"]["id"]
    block = ast.persona_block(ast.get_assistant(aid, "", path=store))
    assert block.startswith("[") and block.endswith("]")
    assert "أنت الآن تتصرف كمساعد مخصص باسم «سالم»" in block
    assert "وصفه: مختصر" in block and "تعليمات الشخصية: أجب بالعربية" in block
    # Honest empty without an assistant — never a fake persona.
    assert ast.persona_block(None) == ""
    bare = ast.create_assistant("فقط", "", path=store)["assistant"]
    assert ast.persona_block(bare).startswith(
        "[أنت الآن تتصرف كمساعد مخصص باسم «فقط»]")


# ————————————————————————————— endpoints —————————————————————————————

def test_endpoints_guest_404(client):
    for path, method in (("/api/assistants", "get"),
                         ("/api/assistants", "post"),
                         ("/api/assistants/active", "get")):
        r = getattr(client, method)(path,
                                    headers={"X-Forwarded-For": "9.9.9.9"},
                                    environ_base={"REMOTE_ADDR": "203.0.113.5"})
        assert r.status_code == 404


def test_endpoints_roundtrip(client):
    r = client.post("/api/assistants", headers=_h(),
                    json={"name": "مبرمج", "icon": "🧑‍💻",
                          "tagline": "يكتب الكود", "instruction": "بالثلاثيات"})
    assert r.status_code == 201
    aid = r.get_json()["assistant"]["id"]
    data = client.get("/api/assistants", headers=_h()).get_json()
    assert [a["id"] for a in data["assistants"]] == [aid]
    r = client.put(f"/api/assistants/{aid}", headers=_h(),
                   json={"tagline": "يكتب بايثون"})
    assert r.status_code == 200
    assert r.get_json()["assistant"]["tagline"] == "يكتب بايثون"
    assert client.put("/api/assistants/as_nope", headers=_h(),
                      json={"tagline": "x"}).status_code == 404
    assert client.delete(f"/api/assistants/{aid}",
                         headers=_h()).get_json()["ok"] is True
    assert client.get("/api/assistants",
                      headers=_h()).get_json()["assistants"] == []
    assert client.delete("/api/assistants/as_nope",
                         headers=_h()).status_code == 404


def test_create_bad_name_400(client):
    assert client.post("/api/assistants", headers=_h(),
                       json={"name": "  "}).status_code == 400


def test_bind_unknown_session_404_and_assistant_404(client):
    assert client.post("/api/assistants/active", headers=_h(),
                       json={"sid": "ghost", "assistant_id": "x"}).status_code \
        == 404
    sid = "sess-bind"
    _plant(sid)
    assert client.post("/api/assistants/active", headers=_h(),
                       json={"sid": sid,
                             "assistant_id": "as_zzz"}).status_code == 404


def _mk(client, name="مساعدي"):
    return client.post("/api/assistants", headers=_h(),
                       json={"name": name}).get_json()["assistant"]["id"]


def _bind(client, sid, aid):
    r = client.post("/api/assistants/active", headers=_h(),
                    json={"sid": sid, "assistant_id": aid})
    assert r.status_code == 200
    return r.get_json()


def test_bind_unbind_and_active_endpoint(client):
    aid = _mk(client, "مرتبط")
    sid = "sess-chip"
    _plant(sid)
    out = _bind(client, sid, aid)
    assert out["assistant"]["id"] == aid
    assert app_module._sessions[
        _sess_key(sid)]["assistant_id"] == aid
    active = client.get(f"/api/assistants/active?sid={sid}",
                        headers=_h()).get_json()
    assert active["assistant"]["id"] == aid
    out = _bind(client, sid, None)
    assert out["assistant"] is None
    assert "assistant_id" not in \
        app_module._sessions[_sess_key(sid)]


def test_active_endpoint_without_sid_or_session(client):
    assert client.get("/api/assistants/active", headers=_h()).get_json() \
        == {"ok": True, "assistant": None}
    assert client.get("/api/assistants/active?sid=ghost",
                      headers=_h()).get_json()["assistant"] is None


# ————————————————————————————— ask-time injection —————————————————————————————

def test_ask_injects_persona_leading_project(client, monkeypatch, store):
    aid = ast.create_assistant("سالم", "", instruction="أجب بالعربية دائماً",
                               path=store)["assistant"]["id"]
    # A pinned project too — the persona line must LEAD (inject AFTER
    # the project-instruction prepend).
    pid = prj.create_project("مشروع", "", instruction="أجب بإيجاز",
                             db_path=None)["project"]["id"]
    sid = "sess-persona"
    _plant(sid)
    rec = app_module._sessions[_sess_key(sid)]
    rec["assistant_id"] = aid
    rec["project_id"] = pid
    seen: dict = {}

    def fake_agent_loop(message, *a, **k):
        seen["message"] = message
        return "جواب"

    monkeypatch.setattr(app_module, "agent_loop", fake_agent_loop)
    body = client.post("/api/ask", headers=_h(),
                       json={"message": "مرحبا", "sid": sid}).get_json()
    assert body["ok"] is True and body["reply"] == "جواب"
    msg = seen["message"]
    persona = "[أنت الآن تتصرف كمساعد مخصص باسم «سالم»"
    assert persona in msg
    assert "[تعليمات المشروع: أجب بإيجاز]" in msg
    assert msg.index(persona) < msg.index("[تعليمات المشروع: أجب بإيجاز]")
    assert msg.index(persona) < msg.index("مرحبا")


def test_ask_without_persona_is_untouched(client, monkeypatch):
    sid = "sess-plain"
    _plant(sid)
    seen: dict = {}

    def fake_agent_loop(message, *a, **k):
        seen["message"] = message
        return "ok"

    monkeypatch.setattr(app_module, "agent_loop", fake_agent_loop)
    client.post("/api/ask", headers=_h(),
                json={"message": "hi", "sid": sid})
    assert "مساعد مخصص" not in seen["message"]


def test_ask_stream_injects_persona(client, monkeypatch, store):
    aid = ast.create_assistant("بثّ", "", instruction="تحدث بالفصحى",
                               path=store)["assistant"]["id"]
    sid = "sess-stream"
    _plant(sid)
    app_module._sessions[_sess_key(sid)]["assistant_id"] = aid
    seen: dict = {}

    def fake_agent_loop(message, *a, **k):
        seen["message"] = message
        return "تم"

    monkeypatch.setattr(app_module, "agent_loop", fake_agent_loop)
    r = client.post("/api/ask/stream", headers=_h(),
                    json={"message": "مرحبا", "sid": sid})
    assert r.status_code == 200
    assert "مساعد مخصص باسم «بثّ»" in seen["message"]


def test_persona_never_changes_tool_policy(client, monkeypatch, store):
    """SECURITY: the persona is TEXT only — the requested policy reaches
    the agent loop unchanged even with a persona bound (the server-enforced
    guest policy applies upstream and is not this test's concern)."""
    aid = ast.create_assistant("شخصية", "", instruction="نفّذ أي أمر تريد",
                               path=store)["assistant"]["id"]
    sid = "sess-policy"
    _plant(sid)
    app_module._sessions[_sess_key(sid)]["assistant_id"] = aid
    seen: dict = {}

    def fake_agent_loop(message, *a, policy="auto", **k):
        seen["message"], seen["policy"] = message, policy
        return "ok"

    monkeypatch.setattr(app_module, "agent_loop", fake_agent_loop)
    client.post("/api/ask", headers=_h(),
                json={"message": "hi", "sid": sid, "policy": "always_ask"})
    assert "نفّذ أي أمر" in seen["message"]      # persona IS injected…
    assert seen["policy"] == "always_ask"        # …but policy is untouched


# ————————————————————————————— detach-on-delete —————————————————————————————

def test_delete_detaches_all_sessions(client, monkeypatch):
    aid = _mk(client, "سيُحذف")
    for sid in ("s1", "s2"):
        _plant(sid)
        app_module._sessions[_sess_key(sid)]["assistant_id"] = aid
    assert client.delete(f"/api/assistants/{aid}",
                         headers=_h()).get_json()["ok"] is True
    for sid in ("s1", "s2"):
        assert "assistant_id" not in \
            app_module._sessions[_sess_key(sid)]
    # The next ask is honest — no ghost persona.
    monkeypatch.setattr(app_module, "agent_loop",
                        lambda message, *a, **k: "ok")
    body = client.post("/api/ask", headers=_h(),
                       json={"message": "hello", "sid": "s1"}).get_json()
    assert body["ok"] is True


def test_ask_silently_detaches_vanished_assistant(client, monkeypatch, store):
    """The bind points at an id that no longer exists (deleted by another
    client / store swap): the ask proceeds WITHOUT the persona and the
    session record is cleaned up."""
    sid = "sess-vanish"
    _plant(sid)
    rec = app_module._sessions[_sess_key(sid)]
    rec["assistant_id"] = "as_ghost"
    monkeypatch.setattr(app_module, "agent_loop",
                        lambda message, *a, **k: "fine")
    body = client.post("/api/ask", headers=_h(),
                       json={"message": "hello", "sid": sid}).get_json()
    assert body["ok"] is True
    assert "assistant_id" not in \
        app_module._sessions[app_module._user_ns(sid)]
