"""SQLite FTS5 search index over all conversations (Wave 1 #2, 2026-09-25).

Design notes
------------
- stdlib sqlite3 only — no Elasticsearch/Meilisearch (owner rule).
- FTS5 virtual table `messages`: `content` is the only tokenized column;
  identity/scope columns are UNINDEXED metadata on the same row.
- tokenize='unicode61' tokenizes Arabic words correctly (diacritics are
  separate tokens, matching is word-based; no stemming — fine for chat).
- prefix='2 3 4' gives fast prefix queries ("برو" → برومبت) via the
  automatic prefix indexes.
- Multi-user safety: every row carries `ns` — the _user_ns session
  namespace prefix ("u<key_id>:" for issued keys, "u<master>:" for the
  owner, "" in local mode). Queries are ns-filtered SERVER-SIDE; role
  awareness never happens in the client. Guests are refused at the
  endpoint layer (403), never here.
- WAL journal: the brain server writes while readers read without locks.
- Logging is CONTENT-FREE: sids/counts only, never message text and
  never the raw query (D:/hwk-data/search_index.log).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("D:/hwk-data/search/aali_search.db")
LOG_FILE = Path("D:/hwk-data/search_index.log")

_MARK_OPEN, _MARK_CLOSE, _ELLIPSIS = "<mark>", "</mark>", "…"
_SNIPPET_TOKENS = 32  # tokens of context around the match (owner spec)

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    content,
    ns         UNINDEXED,
    sid        UNINDEXED,
    role       UNINDEXED,
    ts         UNINDEXED,
    turn_idx   UNINDEXED,
    project_id UNINDEXED
);
"""


def _db(db_path: Path | str | None) -> Path:
    """Resolve at CALL time so tests can monkeypatch DEFAULT_DB."""
    return Path(db_path) if db_path is not None else DEFAULT_DB


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = _db(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _log(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {line}\n")
    except OSError:
        pass  # best-effort, never breaks a search


def init_db(db_path: Path | str | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(_SCHEMA)


def _fts_query(user_query: str) -> str:
    """Build a safe FTS5 MATCH expression from raw user text.

    Every whitespace token is quoted (internal double quotes doubled) so
    punctuation/FTS syntax can never inject the grammar, then given the
    prefix operator: "tok"* matches tok*, toked, توكنة... (unicode61 has
    no stemming, so Arabic's attached ال/و/ب prefixes stay distinct
    tokens — prefix matching is the honest recall win without any
    destructive normalization of the indexed text). Tokens are ANDed.
    Returns "" when nothing searchable remains."""
    tokens = [t for t in user_query.split() if t]
    quoted = ['"' + t.replace('"', '""') + '"*' for t in tokens]
    return " AND ".join(quoted)


def index_message(ns: str, sid: str, role: str, content: str, ts: float,
                  turn_idx: int, project_id: str = "",
                  db_path: Path | str | None = None) -> None:
    if role not in ("user", "assistant") or not content:
        return
    with _connect(db_path) as conn:
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO messages(content, ns, sid, role, ts, turn_idx, "
            "project_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (content, ns, sid, role, float(ts), int(turn_idx),
             str(project_id or "")))
    _log(f"index sid={sid} turn={turn_idx} role={role}")


def reindex_session(record: dict[str, Any],
                    db_path: Path | str | None = None) -> int:
    """Replace one conversation's rows wholesale; idempotent by design.

    The record's `key` field IS the _user_ns value ("<ns>:<sid>" or a bare
    sid in local mode); the ns prefix is everything before the LAST colon.
    Returns the number of rows indexed."""
    key = str(record.get("key") or "")
    sid = str(record.get("sid") or "")
    ns = key.rsplit(":", 1)[0] if ":" in key else ""
    turns = record.get("turns") if isinstance(record.get("turns"), list) else []
    project = str(record.get("project_id") or "")
    with _connect(db_path) as conn:
        conn.execute(_SCHEMA)
        conn.execute("DELETE FROM messages WHERE ns = ? AND sid = ?", (ns, sid))
        rows = [
            (str(t.get("content", "")), ns, sid, str(t.get("role", "")),
             float(t.get("ts", 0.0) or 0.0), i, project)
            for i, t in enumerate(turns)
            if t.get("role") in ("user", "assistant") and t.get("content")
        ]
        conn.executemany(
            "INSERT INTO messages(content, ns, sid, role, ts, turn_idx, "
            "project_id) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    _log(f"reindex sid={sid} rows={len(rows)}")
    return len(rows)


def remove_session(ns: str, sid: str,
                   db_path: Path | str | None = None) -> int:
    with _connect(db_path) as conn:
        conn.execute(_SCHEMA)
        cur = conn.execute(
            "DELETE FROM messages WHERE ns = ? AND sid = ?", (ns, sid))
        removed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    _log(f"remove sid={sid} rows={removed}")
    return removed


def search(query: str, ns: str | None = "", *, role: str | None = None,
           sid: str | None = None, ts_from: float | None = None,
           ts_to: float | None = None, project_id: str | None = None,
           limit: int = 20, offset: int = 0,
           db_path: Path | str | None = None) -> dict[str, Any]:
    """Role-aware, server-side-filtered FTS search.

    ns=None means NO scope filter (admin: all users). ns="" is local mode.
    Remote guests must be refused BEFORE calling this."""
    match = _fts_query(query)
    if not match:
        return {"ok": True, "total": 0, "results": []}
    where = ["messages MATCH ?"]
    params: list[Any] = [match]
    if ns is not None:
        where.append("ns = ?")
        params.append(ns)
    if role in ("user", "assistant"):
        where.append("role = ?")
        params.append(role)
    if sid:
        where.append("sid = ?")
        params.append(sid)
    if ts_from is not None:
        where.append("ts >= ?")
        params.append(float(ts_from))
    if ts_to is not None:
        where.append("ts <= ?")
        params.append(float(ts_to))
    if project_id:
        where.append("project_id = ?")
        params.append(str(project_id))
    where_sql = " AND ".join(where)
    try:
        with _connect(db_path) as conn:
            conn.execute(_SCHEMA)
            total = conn.execute(
                f"SELECT count(*) FROM messages WHERE {where_sql}",
                params).fetchone()[0]
            # NOTE: qmark placeholders bind in SQL-TEXT order — snippet()'s
            # four args appear in the SELECT clause, BEFORE the WHERE
            # params, so they must be bound FIRST (2026-09-25 lesson: the
            # count worked while every rows query silently returned 0).
            rows = conn.execute(
                "SELECT sid, role, "
                "snippet(messages, 0, ?, ?, ?, ?), ts, turn_idx, "
                "bm25(messages) "
                f"FROM messages WHERE {where_sql} "
                "ORDER BY bm25(messages), ts DESC LIMIT ? OFFSET ?",
                [_MARK_OPEN, _MARK_CLOSE, _ELLIPSIS, _SNIPPET_TOKENS,
                 *params, int(limit), int(offset)]).fetchall()
    except sqlite3.OperationalError as exc:
        # Malformed MATCH expression (e.g. a lone quote) — degrade to a
        # single fully-quoted phrase once, then give up honestly.
        _log(f"search error={type(exc).__name__} fallback-tried")
        if '"' not in query:
            return search(f'"{query.strip()}"', ns, role=role, sid=sid,
                          ts_from=ts_from, ts_to=ts_to,
                          project_id=project_id, limit=limit,
                          offset=offset, db_path=db_path)
        return {"ok": True, "total": 0, "results": []}
    results = [
        {
            "message_id": f"{r[0]}:{r[4]}",
            "session_id": r[0],
            "role": r[1],
            "snippet": r[2],
            "timestamp": r[3],
            "score": round(float(r[5]), 4),
        }
        for r in rows
    ]
    _log(f"search terms={len(query.split())} total={total} returned={len(results)}")
    return {"ok": True, "total": int(total), "results": results}


def session_row_count(ns: str, sid: str,
                      db_path: Path | str | None = None) -> int:
    """Rows already indexed for one conversation (backfill skip check)."""
    try:
        with _connect(db_path) as conn:
            conn.execute(_SCHEMA)
            row = conn.execute(
                "SELECT count(*) FROM messages WHERE ns = ? AND sid = ?",
                (ns, sid)).fetchone()
            return int(row[0])
    except sqlite3.Error:
        return 0


def count(ns: str | None = "", db_path: Path | str | None = None) -> int:
    try:
        with _connect(db_path) as conn:
            conn.execute(_SCHEMA)
            if ns is None:
                row = conn.execute("SELECT count(*) FROM messages").fetchone()
            else:
                row = conn.execute(
                    "SELECT count(*) FROM messages WHERE ns = ?",
                    (ns,)).fetchone()
            return int(row[0])
    except sqlite3.Error:
        return 0


def backfill(sessions: dict[str, dict[str, Any]],
             db_path: Path | str | None = None) -> int:
    """Index every conversation once; safe to re-run (reindex_session
    deletes+reinserts per sid). Returns total rows indexed."""
    total = 0
    for record in sessions.values():
        try:
            total += reindex_session(record, db_path)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            _log(f"backfill error={type(exc).__name__} sid={record.get('sid')}")
    _log(f"backfill sessions={len(sessions)} rows={total}")
    return total
