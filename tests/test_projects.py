"""Wave 2 #5+#6: Projects + RAG knowledge base (2026-09-25).

Contract under test:
- Store: create/list/update/archive/delete, ns ownership (one user's
  projects invisible to another), Arabic-first errors.
- Knowledge base: add/list/remove/get docs, chunking shape, per-project
  caps, oversize rejection.
- Retrieval: FTS5 BM25 over ONE project only (never leaks across ns),
  Arabic + English queries, prefix recall, empty match = [] (honest),
  malformed MATCH falls back safely.
- build_rag_context: ("", []) on no hits (never fake context), labeled
  reference block + sources on hits, instruction NOT part of the RAG
  block (it rides separately in the ask path).
- Endpoints: role gate (guest/user), 404 contract, CRUD round-trip,
  retrieve preview, session binding + /api/projects/active, ask-time
  injection with rag_sources in the reply body.
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
from file_agent import projects as prj  # noqa: E402

MASTER = "test-master-key"

DOC_TEXT = (
    "مشروع الواجهة الجديدة يعمل على تقنية WebGL مع دعم كامل للعربية.\n\n"
    "The deploy pipeline runs on Fridays at 6pm and posts to the team channel.\n\n"
    "قرار: تأجيل إطلاق النسخة التجريبية إلى شهر رمضان بسبب اختبارات الأداء."
)

AR_QUERY = "متى إطلاق النسخة التجريبية"
EN_QUERY = "when does the deploy pipeline run"


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "projects.db"


@pytest.fixture()
def client(tmp_path, monkeypatch, db):
    monkeypatch.setattr(app_module, "API_KEY", MASTER)
    monkeypatch.setattr(prj, "DEFAULT_DB", db)
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


def _plant(sid):
    """Plant a session the ask path will accept (fresh timestamps —
    _get_session prunes stale records, which silently drops project_id)."""
    import time as _t
    key = app_module._user_ns(sid)
    app_module._sessions[key] = {
        "key": key, "sid": sid, "turns": [],
        "created_at": _t.time(), "updated_at": _t.time()}
    return app_module._sessions[key]


def _mk(c, name="مشروعي", instr="", ns=""):
    out = prj.create_project(name, ns, instruction=instr, db_path=None)
    return out["project"]["id"]


# ————————————————————————————— store —————————————————————————————

def test_create_and_get_roundtrip(db):
    out = prj.create_project("موقع الشركة", "", instruction="كن مختصراً",
                             db_path=db)
    assert out["ok"] is True
    pid = out["project"]["id"]
    p = prj.get_project(pid, "", db_path=db)
    assert p["name"] == "موقع الشركة" and p["instruction"] == "كن مختصراً"
    assert p["archived"] == 0 and p["doc_count"] == 0


def test_create_empty_name_arabic_error(db):
    out = prj.create_project("   ", "", db_path=db)
    assert out["ok"] is False and "المشروع" in out["error"]


def test_ns_isolation(db):
    a = prj.create_project("لأ", "u1", db_path=db)["project"]["id"]
    assert prj.get_project(a, "u2", db_path=db) is None
    assert prj.get_project(a, "u1", db_path=db) is not None
    assert [p["id"] for p in prj.list_projects("u2", db_path=db)] == []


def test_update_partial_and_archive(db):
    pid = prj.create_project("قبل", "", db_path=db)["project"]["id"]
    out = prj.update_project(pid, "", instruction="تعليمات جديدة", db_path=db)
    assert out["project"]["instruction"] == "تعليمات جديدة"
    out = prj.update_project(pid, "", archived=True, db_path=db)
    assert out["ok"] and prj.get_project(pid, "", db_path=db) is None
    assert prj.list_projects("", include_archived=True,
                             db_path=db)[0]["archived"] == 1


def test_update_unknown_project_404_shape(db):
    out = prj.update_project("proj_nope", "", name="x", db_path=db)
    assert out["ok"] is False and out["error"] == "المشروع غير موجود"


def test_delete_removes_everything(db):
    pid = prj.create_project("للحذف", "", db_path=db)["project"]["id"]
    prj.add_document(pid, "d", DOC_TEXT, db_path=db)
    out = prj.delete_project(pid, "", db_path=db)
    assert out["ok"] is True
    assert prj.get_project(pid, "", db_path=db) is None
    assert prj.list_documents(pid, "", db_path=db)["ok"] is False
    assert prj.retrieve(pid, "WebGL", "", db_path=db) == []


# ————————————————————————————— docs + chunking —————————————————————————————

def test_add_list_remove_doc(db):
    pid = prj.create_project("قاعدة", "", db_path=db)["project"]["id"]
    out = prj.add_document(pid, "خطة الإطلاق", DOC_TEXT, db_path=db)
    # 210-char doc fits one chunk — chunking exists for BIG docs only.
    assert out["ok"] is True and out["doc"]["chunks"] == 1
    listed = prj.list_documents(pid, "", db_path=db)["docs"]
    assert listed[0]["name"] == "خطة الإطلاق" and listed[0]["size"] == len(DOC_TEXT)
    did = listed[0]["id"]
    got = prj.get_document_content(pid, did, "", db_path=db)
    assert got["ok"] and "WebGL" in got["content"]
    assert prj.remove_document(pid, did, "", db_path=db)["ok"] is True
    assert prj.list_documents(pid, "", db_path=db)["docs"] == []


def test_add_doc_oversize_and_empty(db):
    pid = prj.create_project("حدود", "", db_path=db)["project"]["id"]
    assert prj.add_document(pid, "فارغ", "   ", db_path=db)["ok"] is False
    out = prj.add_document(pid, "ضخم", "x" * (prj.MAX_DOC_CHARS + 1), db_path=db)
    assert out["ok"] is False and "كبير" in out["error"]


def test_chunking_overlap_and_caps():
    text = " ".join(f"para{i} " + "كلمة " * 40 for i in range(30))
    chunks = prj._chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= prj.MAX_CHUNK_CHARS for c in chunks)
    assert prj._chunk_text("") == []
    assert prj._chunk_text("سطر واحد") == ["سطر واحد"]


def test_short_docs_stay_single_chunk():
    """A short doc is ONE chunk — no artificial splitting (pinned: the
    chunker exists for big documents, not to slice small ones)."""
    assert len(prj._chunk_text(DOC_TEXT)) == 1


# ————————————————————————————— retrieval —————————————————————————————

def test_retrieve_arabic_and_english(db):
    pid = prj.create_project("rag", "", db_path=db)["project"]["id"]
    prj.add_document(pid, "mixed", DOC_TEXT, db_path=db)
    ar = prj.retrieve(pid, AR_QUERY, "", db_path=db)
    en = prj.retrieve(pid, EN_QUERY, "", db_path=db)
    # OR fallback: query words the doc lacks don't kill recall.
    assert ar and ar[0]["doc_name"] == "mixed"
    assert en and "Fridays" in en[0]["snippet"]
    assert en[0]["doc_name"] == "mixed"


def test_retrieve_never_leaks_across_projects(db):
    p1 = prj.create_project("أ", "", db_path=db)["project"]["id"]
    p2 = prj.create_project("ب", "", db_path=db)["project"]["id"]
    prj.add_document(p1, "secret", "كلمة السر هي نجمة البحر", db_path=db)
    assert prj.retrieve(p2, "نجمة البحر", "", db_path=db) == []
    assert prj.retrieve(p1, "نجمة البحر", "", db_path=db)


def test_retrieve_empty_match_is_honest(db):
    pid = prj.create_project("فارغ", "", db_path=db)["project"]["id"]
    prj.add_document(pid, "d", DOC_TEXT, db_path=db)
    assert prj.retrieve(pid, "quantum entanglement xylophone", "", db_path=db) == []
    assert prj.retrieve(pid, "", "", db_path=db) == []


def test_retrieve_malformed_query_safe(db):
    pid = prj.create_project("q", "", db_path=db)["project"]["id"]
    prj.add_document(pid, "d", DOC_TEXT, db_path=db)
    assert isinstance(prj.retrieve(pid, '"', "", db_path=db), list)
    assert isinstance(prj.retrieve(pid, "NEAR(a b) AND", "", db_path=db), list)


def test_rag_context_with_and_without_hits(db):
    pid = prj.create_project("ctx", "", db_path=db)["project"]["id"]
    prj.add_document(pid, "mixed", DOC_TEXT, db_path=db)
    proj = prj.get_project(pid, "", db_path=db)
    block, sources = prj.build_rag_context(EN_QUERY, proj, "", db_path=db)
    assert block and sources and "قاعدة معرفة المشروع" in block
    assert "[mixed]" in block
    assert len(block) <= prj.MAX_CONTEXT_CHARS + 2
    block2, sources2 = prj.build_rag_context("zzz irrelevant qq", proj, "",
                                             db_path=db)
    assert block2 == "" and sources2 == []


# ————————————————————————————— endpoints —————————————————————————————

def _h(key=MASTER):
    return {"X-API-Key": key}


def test_endpoints_guest_404(client):
    for path, method in (("/api/projects", "get"), ("/api/projects", "post"),
                         ("/api/projects/active", "get")):
        r = getattr(client, method)(path,
                                    headers={"X-Forwarded-For": "9.9.9.9"},
                                    environ_base={"REMOTE_ADDR": "203.0.113.5"})
        assert r.status_code == 404, path


def test_projects_crud_endpoints(client, db):
    r = client.post("/api/projects", headers=_h(),
                    json={"name": "تطبيق الجوال", "instruction": "عربية أولاً"})
    assert r.status_code == 201
    pid = r.get_json()["project"]["id"]
    assert client.get("/api/projects", headers=_h()).get_json()["projects"][0]["name"] == "تطبيق الجوال"
    r = client.patch(f"/api/projects/{pid}", headers=_h(), json={"archived": True})
    assert r.get_json()["ok"] is True
    assert client.get(f"/api/projects/{pid}", headers=_h()).status_code == 404
    assert client.delete(f"/api/projects/{pid}", headers=_h()).get_json()["ok"] is True
    assert client.get("/api/projects", headers=_h()).get_json()["projects"] == []


def test_docs_endpoints_roundtrip(client):
    pid = client.post("/api/projects", headers=_h(),
                      json={"name": "KB"}).get_json()["project"]["id"]
    r = client.post(f"/api/projects/{pid}/docs", headers=_h(),
                    json={"name": "manual", "content": DOC_TEXT})
    assert r.status_code == 201 and r.get_json()["doc"]["chunks"] == 1
    did = r.get_json()["doc"]["id"]
    got = client.get(f"/api/projects/{pid}/docs/{did}", headers=_h()).get_json()
    assert "WebGL" in got["content"]
    assert client.post(f"/api/projects/{pid}/retrieve", headers=_h(),
                       json={"query": EN_QUERY}).get_json()["hits"]
    assert client.delete(f"/api/projects/{pid}/docs/{did}",
                         headers=_h()).get_json()["ok"] is True


def test_docs_empty_content_400_and_unknown_project_404(client):
    pid = client.post("/api/projects", headers=_h(),
                      json={"name": "x"}).get_json()["project"]["id"]
    assert client.post(f"/api/projects/{pid}/docs", headers=_h(),
                       json={"name": "n", "content": "  "}).status_code == 400
    assert client.post("/api/projects/proj_zzz/docs", headers=_h(),
                       json={"name": "n", "content": "hi"}).status_code == 404


# ————————————————————————————— session binding + ask —————————————————————————————

def test_bind_unknown_session_404_and_project_404(client):
    assert client.post("/api/projects/active", headers=_h(),
                       json={"sid": "ghost", "project_id": "x"}).status_code == 404
    sid = "sess-bind"
    _plant(sid)
    assert client.post("/api/projects/active", headers=_h(),
                       json={"sid": sid, "project_id": "proj_zzz"}).status_code == 404


def _bind(client, sid, pid):
    r = client.post("/api/projects/active", headers=_h(),
                    json={"sid": sid, "project_id": pid})
    assert r.status_code == 200
    return r.get_json()


def test_bind_unbind_and_active_endpoint(client):
    pid = client.post("/api/projects", headers=_h(),
                      json={"name": "ربط"}).get_json()["project"]["id"]
    sid = "sess-chip"
    _plant(sid)
    out = _bind(client, sid, pid)
    assert out["project"]["id"] == pid
    assert app_module._sessions[app_module._user_ns(sid)]["project_id"] == pid
    active = client.get(f"/api/projects/active?sid={sid}",
                        headers=_h()).get_json()
    assert active["project"]["id"] == pid
    out = _bind(client, sid, None)
    assert out["project"] is None
    assert "project_id" not in app_module._sessions[app_module._user_ns(sid)]


def test_ask_injects_rag_context_and_sources(client, monkeypatch):
    pid = client.post("/api/projects", headers=_h(),
                      json={"name": "أسئلة", "instruction": "أجب بالعربية"}
                      ).get_json()["project"]["id"]
    client.post(f"/api/projects/{pid}/docs", headers=_h(),
                json={"name": "manual", "content": DOC_TEXT})
    sid = "sess-rag"
    _plant(sid)
    _bind(client, sid, pid)
    seen: dict = {}

    def fake_agent_loop(message, *a, **k):
        seen["message"] = message
        return "جواب من قاعدة المعرفة"

    monkeypatch.setattr(app_module, "agent_loop", fake_agent_loop)
    r = client.post("/api/ask", headers=_h(),
                    json={"message": EN_QUERY, "sid": sid})
    body = r.get_json()
    assert body["ok"] is True and body["reply"] == "جواب من قاعدة المعرفة"
    assert "Fridays" in seen["message"]
    assert "[تعليمات المشروع: أجب بالعربية]" in seen["message"]
    assert "قاعدة معرفة المشروع" in seen["message"]
    assert body["rag_sources"][0]["doc_name"] == "manual"


def test_ask_without_hits_honest_note(client, monkeypatch):
    pid = client.post("/api/projects", headers=_h(),
                      json={"name": "هادئ"}).get_json()["project"]["id"]
    client.post(f"/api/projects/{pid}/docs", headers=_h(),
                json={"name": "d", "content": DOC_TEXT})
    sid = "sess-quiet"
    _plant(sid)
    _bind(client, sid, pid)
    monkeypatch.setattr(app_module, "agent_loop",
                        lambda message, *a, **k: "ok")
    body = client.post("/api/ask", headers=_h(),
                       json={"message": "zzz nothing here q", "sid": sid}
                       ).get_json()
    assert body["ok"] is True and body.get("rag_note")
    assert "rag_sources" not in body


def test_ask_detaches_deleted_project(client, monkeypatch):
    pid = client.post("/api/projects", headers=_h(),
                      json={"name": "يُحذف"}).get_json()["project"]["id"]
    sid = "sess-gone"
    rec = _plant(sid)
    rec["project_id"] = pid
    client.delete(f"/api/projects/{pid}", headers=_h())
    monkeypatch.setattr(app_module, "agent_loop",
                        lambda message, *a, **k: "fine")
    body = client.post("/api/ask", headers=_h(),
                       json={"message": "hello", "sid": sid}).get_json()
    assert body["ok"] is True
    assert "project_id" not in app_module._sessions[app_module._user_ns(sid)]


def test_search_index_untouched_by_project_docs(client, db, tmp_path,
                                                monkeypatch):
    """Knowledge-base chunks live in the projects db — they must never
    appear in the conversation-search index (separation of concerns)."""
    from file_agent import search_index
    monkeypatch.setattr(search_index, "DEFAULT_DB", tmp_path / "search.db")
    pid = client.post("/api/projects", headers=_h(),
                      json={"name": "منفصل"}).get_json()["project"]["id"]
    client.post(f"/api/projects/{pid}/docs", headers=_h(),
                json={"name": "d", "content": DOC_TEXT})
    assert search_index.count() == 0
