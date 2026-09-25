"""Shared conversations (Wave 3 #8, 2026-09-25).

Read-only, tokened, expiring links to one conversation. The share token
IS the credential (same pattern as the admin handoff): stored
SHA-256-hashed, shown once to the owner, accepted only by the public
/share/<token> page.

Security contract
-----------------
- Owner-only creation/revocation (role `export`/`own_sessions` — enforced
  in app.py; guests get the uniform 404).
- Read-only BY SHAPE: the public page renders a frozen copy of the turns
  at share time; revoking/expiring removes access everywhere instantly.
- Tokens: 32-char urlsafe secrets — never stored in plaintext; the store
  keeps token_hash -> share record.
- Expiry: ttl_hours (default 168 = 7 days, max 24*30). Expired/pruned
  shares answer 404 like everything else.
- Content-free audit: share_create / share_revoke events (never payloads).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

DEFAULT_STORE = Path("D:/hwk-data/shares.jsonl")

DEFAULT_TTL_HOURS = 7 * 24
MAX_TTL_HOURS = 30 * 24
MAX_TURNS = 100
MAX_SHARES = 500          # hard cap; lazy prune keeps the file small


def _hash(token: str) -> str:
    return "sha:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load(path: Path | str | None) -> dict[str, dict[str, Any]]:
    p = Path(path) if path else DEFAULT_STORE
    out: dict[str, dict[str, Any]] = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("token_hash"):
                out[rec["token_hash"]] = rec
    except OSError:
        return {}
    return out


def _save(records: dict[str, dict[str, Any]],
          path: Path | str | None) -> None:
    p = Path(path) if path else DEFAULT_STORE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in records.values():
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(p)


def _prune(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    now = time.time()
    alive = {h: r for h, r in records.items()
             if r.get("expires", 0) > now and not r.get("revoked")}
    # cap: newest survive
    if len(alive) > MAX_SHARES:
        keep = sorted(alive.values(),
                      key=lambda r: r.get("created", 0), reverse=True)
        alive = {r["token_hash"]: r for r in keep[:MAX_SHARES]}
    return alive


def create_share(sid: str, turns: list[dict[str, Any]], *, ns: str = "",
                 title: str = "", ttl_hours: float | None = None,
                 owner: str = "", path: Path | str | None = None
                 ) -> dict[str, Any]:
    """Freeze one conversation into a share record; returns the token ONCE.

    turns must be the already-loaded list of {role, content, ts} — this
    module never touches _sessions (the caller owns that lookup).
    """
    token = secrets.token_urlsafe(24)
    ttl = DEFAULT_TTL_HOURS if ttl_hours is None else min(
        max(float(ttl_hours), 0.1), MAX_TTL_HOURS)
    now = time.time()
    frozen = [
        {"role": str(t.get("role", ""))[:12],
         "content": str(t.get("content", ""))[:20000],
         "ts": float(t.get("ts", 0.0) or 0.0)}
        for t in (turns or [])
        if t.get("role") in ("user", "assistant") and t.get("content")
    ][:MAX_TURNS]
    rec = {
        "token_hash": _hash(token),
        "sid": str(sid), "ns": str(ns or ""),
        "title": str(title or "")[:120],
        "turns": frozen,
        "created": now, "expires": now + ttl * 3600,
        "owner": str(owner or "")[:120],
        "views": 0,
    }
    records = _prune(_load(path))
    records[rec["token_hash"]] = rec
    _save(records, path)
    return {"token": token, "expires": rec["expires"],
            "turns": len(frozen)}


def resolve_share(token: str, path: Path | str | None = None
                  ) -> dict[str, Any] | None:
    """Token -> frozen conversation (and bumps the view counter). Expired,
    revoked, or unknown tokens all look identical: None -> uniform 404."""
    if not token or len(token) > 200:
        return None
    records = _load(path)
    rec = records.get(_hash(token))
    if not rec or rec.get("revoked") or rec.get("expires", 0) < time.time():
        return None
    rec["views"] = int(rec.get("views", 0)) + 1
    _save(records, path)
    return rec


def revoke(token_hash: str, *, ns: str = "", path: Path | str | None = None
           ) -> bool:
    """Revoke by token_hash (the list endpoint hands them out). ns must
    match the creator's namespace — owners can only revoke their own."""
    records = _load(path)
    rec = records.get(token_hash)
    if not rec or (ns is not None and rec.get("ns") != str(ns or "")):
        return False
    rec["revoked"] = True
    _save(_prune(records), path)
    return True


def list_shares(ns: str = "", path: Path | str | None = None
                ) -> list[dict[str, Any]]:
    """Content-free list for the owner: hashes, titles, timestamps —
    NEVER the token (it doesn't exist anymore) nor the turns."""
    now = time.time()
    out = []
    for rec in _prune(_load(path)).values():
        if rec.get("ns") != str(ns or ""):
            continue
        out.append({
            "token_hash": rec["token_hash"],
            "sid": rec.get("sid", ""),
            "title": rec.get("title", ""),
            "created": rec.get("created", 0),
            "expires": rec.get("expires", 0),
            "expired": rec.get("expires", 0) < now,
            "views": rec.get("views", 0),
            "turn_count": len(rec.get("turns", [])),
        })
    out.sort(key=lambda r: r["created"], reverse=True)
    return out


def purge_expired(path: Path | str | None = None) -> int:
    records = _prune(_load(path))
    before = len(_load(path))
    _save(records, path)
    return before - len(records)
