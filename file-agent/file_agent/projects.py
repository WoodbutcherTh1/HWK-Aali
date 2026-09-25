"""Projects + RAG knowledge base (Wave 2 #5+#6, 2026-09-25).

The organizing layer everything else plugs into: a project groups
conversations, carries a custom instruction (its "system flavor"), and
holds a per-project document knowledge base. Retrieval is BM25 via the
SAME SQLite FTS5 machinery as conversation search — zero new deps,
embeddings banned until the own-embedding-model phase (priority_plan).

Storage: one SQLite db at D:/hwk-data/projects/aali_projects.db.
 - projects(id TEXT PK, name, instruction, ns, created_at, updated_at,
            archived 0/1)
 - docs(id TEXT PK, project_id, name, source, size, chunks, added_at)
 - doc_chunks(doc_id, project_id, idx, content) + FTS5 virtual table
   doc_chunks_fts(content, project_id, doc_id, idx, UNINDEXED...)

Namespacing: every project belongs to the ns that created it (same
_user_ns contract as search_index: "" locally, "u<key_id>" for key
users). ns=None only for admin tools; retrieval ALWAYS filters by
project_id + ns so one user's knowledge base can never leak into
another's context.

Security invariants:
- content is stored verbatim; NOTHING is ever executed — the ask path
  injects retrieved chunks as clearly-labeled REFERENCE text, and the
  system prompt already treats message content as data, not commands.
- All SQL is qmark-parameterized; FTS MATCH strings go through the same
  quote-every-token grammar as search_index (no FTS syntax injection).
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("D:/hwk-data/projects/aali_projects.db")

MAX_NAME = 80
MAX_INSTRUCTION = 2000
MAX_DOC_NAME = 140
MAX_DOC_CHARS = 200_000        # ~50k tokens: a real doc, not a book dump
MAX_CHUNK_CHARS = 700
CHUNK_OVERLAP = 80
MAX_DOCS_PER_PROJECT = 100
TOP_K_DEFAULT = 4
MAX_CONTEXT_CHARS = 3500       # hard cap on the injected RAG block
MIN_SCORE = 0.0                # bm25() is lower=better; gate at <= this


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  instruction TEXT NOT NULL DEFAULT '',
  ns TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS docs(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'upload',
  size INTEGER NOT NULL DEFAULT 0,
  chunks INTEGER NOT NULL DEFAULT 0,
  added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS doc_chunks(
  doc_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  content TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
  content, project_id UNINDEXED, doc_id UNINDEXED, idx UNINDEXED,
  tokenize='unicode61 remove_diacritics 2'
);
CREATE INDEX IF NOT EXISTS idx_projects_ns ON projects(ns, archived);
CREATE INDEX IF NOT EXISTS idx_docs_proj ON docs(project_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON doc_chunks(doc_id);
"""


def _db(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DB


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = _db(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str | None = None) -> None:
    """Create the schema (multi-statement -> executescript, NOT execute:
    sqlite3 only runs one statement per execute() call)."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def _fts_query(text: str) -> str:
    """Same safe MATCH builder as search_index: quote every token,
    prefix-enabled, ANDed; '' when nothing searchable remains."""
    tokens = [t for t in text.split() if t]
    if not tokens:
        return ""
    return " AND ".join(
        '"' + t.replace('"', '""') + '"*' for t in tokens)


def _chunk_text(text: str) -> list[str]:
    """Overlap-window chunking: paragraphs first, then hard slices."""
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur = ""
    for para in paras:
        while len(para) > MAX_CHUNK_CHARS:          # giant paragraph: slice
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(para[:MAX_CHUNK_CHARS])
            para = para[MAX_CHUNK_CHARS - CHUNK_OVERLAP:]
        if len(cur) + len(para) + 2 <= MAX_CHUNK_CHARS:
            cur = f"{cur}\n\n{para}" if cur else para
        else:
            if cur:
                chunks.append(cur)
            cur = para
    if cur:
        chunks.append(cur)
    return chunks


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}{secrets.token_hex(2)}"


def _log(line: str) -> None:
    try:
        (Path("D:/hwk-data/projects").mkdir(parents=True, exist_ok=True))
        with (Path("D:/hwk-data/projects/log.jsonl")).open(
                "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "line": line},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


# ————————————————————————————— project CRUD —————————————————————————————

def create_project(name: str, ns: str = "", instruction: str = "",
                   db_path: Path | str | None = None) -> dict[str, Any]:
    name = name.strip()[:MAX_NAME]
    if not name:
        return {"ok": False, "error": "اسم المشروع مطلوب"}
    instruction = (instruction or "").strip()[:MAX_INSTRUCTION]
    now = time.time()
    pid = _new_id("proj")
    with _connect(db_path) as conn:
        init_db(db_path)
        conn.execute(
            "INSERT INTO projects(id, name, instruction, ns, created_at, "
            "updated_at, archived) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (pid, name, instruction, str(ns or ""), now, now))
    _log(f"create proj={pid} ns={ns!r} chars_instr={len(instruction)}")
    return {"ok": True, "project": get_project(pid, ns, db_path=db_path)}


def get_project(pid: str, ns: str | None = "",
                db_path: Path | str | None = None) -> dict[str, Any] | None:
    """One project. ns="" (or a value) enforces ownership; ns=None is the
    admin escape hatch and skips the filter. Carries doc_count/doc_chars
    like list_projects rows so callers see real sizes."""
    with _connect(db_path) as conn:
        init_db(db_path)
        if ns is None:
            row = conn.execute(
                "SELECT p.*, "
                "(SELECT count(*) FROM docs d WHERE d.project_id = p.id) AS doc_count, "
                "(SELECT COALESCE(SUM(d.size), 0) FROM docs d "
                " WHERE d.project_id = p.id) AS doc_chars "
                "FROM projects p WHERE p.id = ? AND p.archived = 0",
                (pid,)).fetchone()
        else:
            row = conn.execute(
                "SELECT p.*, "
                "(SELECT count(*) FROM docs d WHERE d.project_id = p.id) AS doc_count, "
                "(SELECT COALESCE(SUM(d.size), 0) FROM docs d "
                " WHERE d.project_id = p.id) AS doc_chars "
                "FROM projects p WHERE p.id = ? AND p.ns = ? "
                "AND p.archived = 0", (pid, str(ns or ""))).fetchone()
        return dict(row) if row else None


def list_projects(ns: str = "", include_archived: bool = False,
                  db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Arabic-first ordering is the CLIENT's job; here: newest first.
    Each row carries doc_count + char total so the UI shows real sizes."""
    with _connect(db_path) as conn:
        init_db(db_path)
        sql = ("SELECT p.*, "
               "(SELECT count(*) FROM docs d WHERE d.project_id = p.id) AS doc_count, "
               "(SELECT COALESCE(SUM(d.size), 0) FROM docs d "
               " WHERE d.project_id = p.id) AS doc_chars "
               "FROM projects p WHERE p.ns = ?")
        params: list[Any] = [str(ns or "")]
        if not include_archived:
            sql += " AND p.archived = 0"
        sql += " ORDER BY p.updated_at DESC"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def update_project(pid: str, ns: str = "", *, name: str | None = None,
                   instruction: str | None = None, archived: bool | None = None,
                   db_path: Path | str | None = None) -> dict[str, Any]:
    """Partial update; at least one field required. Arabic-first errors."""
    cur = get_project(pid, ns, db_path=db_path)
    if cur is None:
        return {"ok": False, "error": "المشروع غير موجود"}
    sets, params = [], []
    if name is not None:
        name = name.strip()[:MAX_NAME]
        if not name:
            return {"ok": False, "error": "الاسم لا يمكن أن يكون فارغاً"}
        sets.append("name = ?"); params.append(name)
    if instruction is not None:
        sets.append("instruction = ?")
        params.append(instruction.strip()[:MAX_INSTRUCTION])
    if archived is not None:
        sets.append("archived = ?")
        params.append(1 if archived else 0)
    if not sets:
        return {"ok": False, "error": "لا يوجد تغيير"}
    sets.append("updated_at = ?"); params.append(time.time())
    params.append(pid)
    with _connect(db_path) as conn:
        init_db(db_path)
        conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?",
                     params)
    _log(f"update proj={pid} fields={len(sets)-1}")
    return {"ok": True, "project": get_project(pid, ns, db_path=db_path)}


def delete_project(pid: str, ns: str = "",
                   db_path: Path | str | None = None) -> dict[str, Any]:
    """Hard delete: project + its docs + chunks (knowledge base included)."""
    with _connect(db_path) as conn:
        init_db(db_path)
        row = conn.execute("SELECT ns FROM projects WHERE id = ?",
                           (pid,)).fetchone()
        if row is None or (ns is not None and row["ns"] != str(ns or "")):
            return {"ok": False, "error": "المشروع غير موجود"}
        conn.execute("DELETE FROM doc_chunks_fts WHERE project_id = ?",
                     (pid,))
        conn.execute("DELETE FROM doc_chunks WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM docs WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
    _log(f"delete proj={pid}")
    return {"ok": True}


# ————————————————————————————— documents + RAG —————————————————————————————

def add_document(pid: str, name: str, content: str, ns: str = "",
                 source: str = "upload",
                 db_path: Path | str | None = None) -> dict[str, Any]:
    """Store one document into the project's knowledge base (chunked +
    FTS-indexed). Arabic-first errors; size-capped; per-project cap."""
    proj = get_project(pid, ns, db_path=db_path)
    if proj is None:
        return {"ok": False, "error": "المشروع غير موجود"}
    name = name.strip()[:MAX_DOC_NAME] or "مستند"
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "المستند فارغ"}
    if len(content) > MAX_DOC_CHARS:
        return {"ok": False,
                "error": f"المستند كبير جداً (الحد {MAX_DOC_CHARS} حرف)"}
    with _connect(db_path) as conn:
        init_db(db_path)
        n_docs = conn.execute(
            "SELECT count(*) FROM docs WHERE project_id = ?",
            (pid,)).fetchone()[0]
        if n_docs >= MAX_DOCS_PER_PROJECT:
            return {"ok": False,
                    "error": f"الحد {MAX_DOCS_PER_PROJECT} مستنداً لكل مشروع"}
        chunks = _chunk_text(content)
        did = _new_id("doc")
        now = time.time()
        conn.execute(
            "INSERT INTO docs(id, project_id, name, source, size, chunks, "
            "added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (did, pid, name, str(source or "upload")[:40], len(content),
             len(chunks), now))
        conn.executemany(
            "INSERT INTO doc_chunks(doc_id, project_id, idx, content) "
            "VALUES (?, ?, ?, ?)",
            [(did, pid, i, c) for i, c in enumerate(chunks)])
        conn.executemany(
            "INSERT INTO doc_chunks_fts(content, project_id, doc_id, idx) "
            "VALUES (?, ?, ?, ?)",
            [(c, pid, did, i) for i, c in enumerate(chunks)])
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?",
                     (now, pid))
    _log(f"doc add proj={pid} doc={did} chunks={len(chunks)} chars={len(content)}")
    return {"ok": True, "doc": {"id": did, "name": name, "size": len(content),
                                "chunks": len(chunks)}}


def list_documents(pid: str, ns: str = "",
                   db_path: Path | str | None = None) -> dict[str, Any]:
    proj = get_project(pid, ns, db_path=db_path)
    if proj is None:
        return {"ok": False, "error": "المشروع غير موجود"}
    with _connect(db_path) as conn:
        init_db(db_path)
        docs = [dict(r) for r in conn.execute(
            "SELECT id, name, source, size, chunks, added_at FROM docs "
            "WHERE project_id = ? ORDER BY added_at DESC", (pid,)).fetchall()]
    return {"ok": True, "docs": docs}


def remove_document(pid: str, doc_id: str, ns: str = "",
                    db_path: Path | str | None = None) -> dict[str, Any]:
    proj = get_project(pid, ns, db_path=db_path)
    if proj is None:
        return {"ok": False, "error": "المشروع غير موجود"}
    with _connect(db_path) as conn:
        init_db(db_path)
        cur = conn.execute(
            "DELETE FROM doc_chunks_fts WHERE doc_id = ?", (doc_id,))
        n1 = cur.rowcount
        cur = conn.execute(
            "DELETE FROM doc_chunks WHERE doc_id = ?", (doc_id,))
        n2 = cur.rowcount
        cur = conn.execute(
            "DELETE FROM docs WHERE id = ? AND project_id = ?",
            (doc_id, pid))
        n3 = cur.rowcount
    if n3 == 0:
        return {"ok": False, "error": "المستند غير موجود"}
    _log(f"doc remove proj={pid} doc={doc_id} fts={n1} chunks={n2}")
    return {"ok": True}


def get_document_content(pid: str, doc_id: str, ns: str = "",
                         db_path: Path | str | None = None) -> dict[str, Any]:
    """Full content of one doc (owner/user reads their own knowledge base)."""
    proj = get_project(pid, ns, db_path=db_path)
    if proj is None:
        return {"ok": False, "error": "المشروع غير موجود"}
    with _connect(db_path) as conn:
        init_db(db_path)
        row = conn.execute(
            "SELECT name FROM docs WHERE id = ? AND project_id = ?",
            (doc_id, pid)).fetchone()
        if row is None:
            return {"ok": False, "error": "المستند غير موجود"}
        chunks = [r["content"] for r in conn.execute(
            "SELECT content FROM doc_chunks WHERE doc_id = ? "
            "ORDER BY idx", (doc_id,)).fetchall()]
    return {"ok": True, "name": row["name"], "content": "\n\n".join(chunks)}


def retrieve(pid: str, query: str, ns: str = "", *, top_k: int = TOP_K_DEFAULT,
             db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """BM25 retrieval over ONE project's knowledge base. Lower bm25() =
    better; ns is enforced; cross-user retrieval impossible.

    Precision-first: try AND-all-tokens; when that finds nothing (a query
    word absent from the docs kills AND — normal for questions), fall back
    to OR-ranked (classic BM25: chunks matching more/scarcer terms rank
    better). Score gate: bm25() <= 0 = at least one real term match."""
    proj = get_project(pid, ns, db_path=db_path)
    if proj is None:
        return []
    tokens = [t for t in (query or "").split() if t]
    if not tokens:
        return []
    quoted = ['"' + t.replace('"', '""') + '"*' for t in tokens]
    match_and = " AND ".join(quoted)
    match_or = " OR ".join(quoted)
    limit = max(1, min(int(top_k), 10))
    rows: list = []
    for match in (match_and, match_or):
        try:
            with _connect(db_path) as conn:
                init_db(db_path)
                rows = conn.execute(
                    "SELECT doc_id, idx, snippet(doc_chunks_fts, 0, ?, ?, ?, ?) "
                    "AS snip, bm25(doc_chunks_fts) AS score "
                    "FROM doc_chunks_fts WHERE doc_chunks_fts MATCH ? "
                    "AND project_id = ? ORDER BY bm25(doc_chunks_fts) "
                    "LIMIT ?",
                    ["[", "]", " … ", 24, match, pid, limit]).fetchall()
        except sqlite3.OperationalError:
            # Malformed MATCH (lone quote etc.) — one honest phrase fallback.
            try:
                phrase = '"' + query.strip().replace('"', '""') + '"'
                with _connect(db_path) as conn:
                    init_db(db_path)
                    rows = conn.execute(
                        "SELECT doc_id, idx, snippet(doc_chunks_fts, 0, ?, ?, ?, ?) "
                        "AS snip, bm25(doc_chunks_fts) AS score "
                        "FROM doc_chunks_fts WHERE doc_chunks_fts MATCH ? "
                        "AND project_id = ? ORDER BY bm25(doc_chunks_fts) LIMIT ?",
                        ["[", "]", " … ", 24, phrase, pid, limit]).fetchall()
            except sqlite3.OperationalError:
                return []
        if rows:
            break
    if not rows:
        return []
    with _connect(db_path) as conn:
        init_db(db_path)
        names = {r["id"]: r["name"] for r in conn.execute(
            "SELECT id, name FROM docs WHERE project_id = ?", (pid,))}
    out = []
    for r in rows:
        score = float(r["score"])
        if score > 0:            # bm25: <= 0 = at least one term matched
            continue
        out.append({
            "doc_id": r["doc_id"], "doc_name": names.get(r["doc_id"], "؟"),
            "chunk": r["idx"], "snippet": r["snip"], "score": round(score, 4),
        })
    return out


def build_rag_context(query: str, project: dict[str, Any], ns: str = "",
                      *, top_k: int = TOP_K_DEFAULT,
                      db_path: Path | str | None = None
                      ) -> tuple[str, list[dict[str, Any]]]:
    """The ask-time injection block + its sources (for the UI citations).

    Honest contract: when nothing matches, returns ("", []) — the ask
    proceeds WITHOUT any fake context, and the caller can tell the user
    the knowledge base had nothing relevant.
    """
    pid = str(project.get("id") or "")
    if not pid or not (query or "").strip():
        return "", []
    hits = retrieve(pid, query, ns, top_k=top_k, db_path=db_path)
    if not hits:
        return "", []
    parts = [f"[{h['doc_name']}] {h['snippet']}" for h in hits]
    block = ("سياق مرجعي من قاعدة معرفة المشروع "
             f"«{project.get('name', '')}» (استخدمه إن كان ملائماً واذكر "
             "اسم المستند عند الاقتباس، ولا تنسب أي شيء إليه إن لم يرد فيه):\n"
             + "\n---\n".join(parts))
    if len(block) > MAX_CONTEXT_CHARS:
        block = block[:MAX_CONTEXT_CHARS] + " …"
    return block, hits


def project_stats(ns: str | None = "",
                  db_path: Path | str | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        init_db(db_path)
        if ns is None:
            row = conn.execute(
                "SELECT count(*) FROM projects WHERE archived = 0").fetchone()
        else:
            row = conn.execute(
                "SELECT count(*) FROM projects WHERE ns = ? AND archived = 0",
                (str(ns or ""),)).fetchone()
    return {"projects": int(row[0])}
