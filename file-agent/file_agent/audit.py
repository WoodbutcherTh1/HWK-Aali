"""Aali audit log — who did what on the admin surface, when, from where.

Every admin action (issue a key, revoke a key, delete an account) is appended
here so the dashboard can show the trail: API keys are visible and revocable,
accounts deletable — an honest system shows its own history.

Design follows file_agent/apikeys.py: stdlib-only, JSONL, thread-safe,
best-effort (an audit write failure must never break the action itself).
Records: {ts, actor, actor_kind, action, target, detail, ip}. The plaintext
key or password never enters a record — targets are key_ids, prefixes,
emails.

Storage: JSONL at ``file-agent/audit_log.jsonl`` (gitignored runtime data).
Appends only; read walks the file newest-last. Trimmed to the newest
``_MAX_LINES`` entries on write so the file can never grow unbounded.

IMPORTANT: this log answers "who did it", never "who asked Aali what" —
chat content stays out on purpose (privacy rule).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "audit_log.jsonl"
# Keep the newest N entries (append-only tail).
_MAX_LINES = 2000

_lock = threading.Lock()
_store_path: Path = DEFAULT_STORE


def _configure(path: str | Path | None = None) -> None:
    """Point the store at a different file (used by tests)."""
    global _store_path
    if path is not None:
        _store_path = Path(path)


def record(
    action: str,
    target: str = "",
    actor: str = "",
    actor_kind: str = "",
    detail: str = "",
    ip: str = "",
) -> dict | None:
    """Append one event; returns the record, or None if the write failed.

    Never raises — the audited action must succeed even if logging fails.
    """
    rec = {
        "ts": time.time(),
        "actor": (actor or "admin")[:120],
        "actor_kind": (actor_kind or "master_key")[:40],
        "action": (action or "")[:40],
        "target": (target or "")[:200],
        "detail": (detail or "")[:300],
        "ip": (ip or "")[:80],
    }
    try:
        with _lock:
            _store_path.parent.mkdir(parents=True, exist_ok=True)
            with _store_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
            _trim()
    except OSError:
        return None
    return rec


def _trim() -> None:
    """Keep only the newest _MAX_LINES entries (called under _lock)."""
    try:
        with _store_path.open(encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    if len(lines) <= _MAX_LINES:
        return
    try:
        with _store_path.open("w", encoding="utf-8") as handle:
            handle.writelines(lines[-_MAX_LINES:])
    except OSError:
        pass


def list_events(limit: int = 100, action: str = "") -> list[dict]:
    """Newest-first audit entries, optionally filtered by action.

    Reads are tolerant: a corrupted line is skipped, a missing/empty file
    returns []. ``limit`` is capped at _MAX_LINES.
    """
    limit = max(1, min(int(limit), _MAX_LINES))
    events: list[dict] = []
    try:
        with _store_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if action and rec.get("action") != action:
                    continue
                events.append(rec)
    except OSError:
        return []
    events.reverse()  # newest first
    return events[:limit]


def clear() -> None:
    """Wipe the log (tests only)."""
    with _lock:
        try:
            _store_path.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_loaded() -> None:
    """No-op for symmetry with apikeys/accounts (the log appends on demand)."""
    return None


def store_path() -> str:
    return str(_store_path)


# Environment override so deployments can move the log (data lives OUTSIDE
# the repo per AGENTS.md): AALI_AUDIT_LOG=D:/hwk-data/audit_log.jsonl
_env = os.getenv("AALI_AUDIT_LOG", "").strip()
if _env:
    _store_path = Path(_env)
