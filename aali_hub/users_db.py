"""Aali Hub user database — SQLite (Postgres migration later).

Tables:
    users        — id, email (unique), password_hash, role, created_at,
                   disabled, email_verified
    api_keys     — key_hash (sha256), user_id, name, created_at, revoked
    usage        — user_id, day (UTC date), requests, tokens_in, tokens_out
    audit_events — append-only JSONL-style rows: ts, actor, action, target,
                   detail (never chat content, never file contents)

Password hashing prefers argon2id (argon2-cffi). When the package is not
installed (e.g. the training venv / Pi CI), a PBKDF2-HMAC-SHA256 fallback is
used so the same code runs everywhere; hash strings are self-describing so
verifying dispatches on the prefix. Production deployments should install
argon2-cffi.

Each method opens its own short-lived connection — thread-safe by design and
immune to cross-thread sqlite handle errors.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # production path
    from argon2 import PasswordHasher as _ArgonHasher
    from argon2.exceptions import VerifyMismatchError

    _HAS_ARGON2 = True
except ImportError:  # stdlib fallback (training venv / Pi CI)
    _HAS_ARGON2 = False

__all__ = ["UsersDB", "UserError", "ROLES", "normalize_role"]

ROLES = ("owner", "admin", "pro_user", "free_user", "external_provider")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_PBKDF2_ITERATIONS = 600_000


class UserError(Exception):
    """Expected, user-facing users_db error."""


def normalize_role(role: str) -> str:
    """Map loose role strings onto the canonical ROLES tuple."""
    key = (role or "").strip().lower()
    aliases = {
        "owner": "owner", "admin": "admin", "root": "owner",
        "pro": "pro_user", "pro_user": "pro_user", "user": "free_user",
        "free": "free_user", "free_user": "free_user",
        "external": "external_provider",
        "external_provider": "external_provider", "provider": "external_provider",
    }
    if key not in aliases:
        raise UserError(f"unknown role: {role!r}")
    return aliases[key]


# ---------------------------------------------------------------------------
# password hashing (argon2id preferred, PBKDF2 fallback)
# ---------------------------------------------------------------------------
if _HAS_ARGON2:
    _argon = _ArgonHasher()

    def _hash_password(password: str) -> str:
        return _argon.hash(password)

    def _verify_password(password: str, stored: str) -> bool:
        if stored.startswith("pbkdf2$"):
            return _pbkdf2_verify(password, stored)
        try:
            return _argon.verify(stored, password)
        except VerifyMismatchError:
            return False
else:

    def _hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     salt, _PBKDF2_ITERATIONS)
        return (f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}")

    def _verify_password(password: str, stored: str) -> bool:
        return _pbkdf2_verify(password, stored)


def _pbkdf2_verify(password: str, stored: str) -> bool:
    """Verify a ``pbkdf2$iter$salt$hash`` record in constant time."""
    try:
        _tag, iterations, salt_hex, hash_hex = stored.split("$", 3)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'free_user',
    created_at      TEXT NOT NULL,
    disabled        INTEGER NOT NULL DEFAULT 0,
    email_verified  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash    TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
    user_id     TEXT NOT NULL,
    day         TEXT NOT NULL,
    requests    INTEGER NOT NULL DEFAULT 0,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS audit_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    target  TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage(day);
"""


class UsersDB:
    """SQLite-backed user/API-key/usage/audit store for the Hub."""

    def __init__(self, db_path: str | Path = "aali_hub.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- users -------------------------------------------------------------
    def create_user(self, email: str, password: str, *,
                    role: str = "free_user",
                    email_verified: bool = False) -> dict[str, Any]:
        """Register a user; returns the public user record."""
        email = (email or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise UserError("invalid email address")
        if len(password or "") < 8:
            raise UserError("password must be at least 8 characters")
        role = normalize_role(role)
        user_id = "u_" + secrets.token_hex(8)
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO users (id, email, password_hash, role,"
                    " created_at, email_verified) VALUES (?,?,?,?,?,?)",
                    (user_id, email, _hash_password(password), role,
                     _now_iso(), int(email_verified)),
                )
        except sqlite3.IntegrityError as exc:
            raise UserError("email already registered") from exc
        return self.get_user(user_id, raise_if_missing=True)

    def verify_login(self, email: str, password: str) -> dict[str, Any] | None:
        """Return the user record on valid credentials, else None."""
        email = (email or "").strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            return None
        if row["disabled"]:
            return None
        if not _verify_password(password or "", row["password_hash"]):
            return None
        return self._public(dict(row))

    def get_user(self, user_id: str, *, raise_if_missing: bool = False
                 ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            if raise_if_missing:
                raise UserError("user not found")
            return None
        return self._public(dict(row))

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        email = (email or "").strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return self._public(dict(row)) if row else None

    def set_role(self, user_id: str, role: str) -> dict[str, Any]:
        role = normalize_role(role)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            if cur.rowcount != 1:
                raise UserError("user not found")
        return self.get_user(user_id, raise_if_missing=True)

    def set_disabled(self, user_id: str, disabled: bool) -> dict[str, Any]:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE users SET disabled = ? WHERE id = ?",
                (int(disabled), user_id))
            if cur.rowcount != 1:
                raise UserError("user not found")
        return self.get_user(user_id, raise_if_missing=True)

    def set_email_verified(self, user_id: str, verified: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET email_verified = ? WHERE id = ?",
                (int(verified), user_id))

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        """Strip password material from a user row."""
        return {k: v for k, v in row.items() if k != "password_hash"}

    # -- api keys ------------------------------------------------------------
    def create_api_key(self, user_id: str, name: str = "") -> str:
        """Mint an API key; returns the plaintext key exactly once."""
        plaintext = "aali-" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO api_keys (key_hash, user_id, name, created_at)"
                " VALUES (?,?,?,?)",
                (key_hash, user_id, name, _now_iso()))
        return plaintext

    def resolve_api_key(self, plaintext: str) -> dict[str, Any] | None:
        """Resolve a bearer API key to its owning user (None if invalid)."""
        key_hash = hashlib.sha256((plaintext or "").encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.* FROM api_keys k JOIN users u ON u.id = k.user_id"
                " WHERE k.key_hash = ? AND k.revoked = 0",
                (key_hash,)).fetchone()
        if row is None or row["disabled"]:
            return None
        return self._public(dict(row))

    def list_api_keys(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key_hash, name, created_at, revoked FROM api_keys"
                " WHERE user_id = ? ORDER BY created_at", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_hash_prefix: str, user_id: str) -> bool:
        """Revoke one of the user's keys identified by a hash prefix."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE key_hash LIKE ?"
                " AND user_id = ? AND revoked = 0",
                (key_hash_prefix + "%", user_id))
            return cur.rowcount == 1

    # -- usage metering ------------------------------------------------------
    def record_usage(self, user_id: str, *, requests: int = 0,
                     tokens_in: int = 0, tokens_out: int = 0,
                     day: str | None = None) -> None:
        target_day = day or _utc_day()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO usage (user_id, day, requests, tokens_in,"
                " tokens_out) VALUES (?,?,?,?,?)"
                " ON CONFLICT(user_id, day) DO UPDATE SET"
                " requests = requests + excluded.requests,"
                " tokens_in = tokens_in + excluded.tokens_in,"
                " tokens_out = tokens_out + excluded.tokens_out",
                (user_id, target_day, requests, tokens_in, tokens_out))

    def usage_today(self, user_id: str) -> dict[str, int]:
        return self.usage_for_day(user_id, _utc_day())

    def usage_for_day(self, user_id: str, day: str) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT requests, tokens_in, tokens_out FROM usage"
                " WHERE user_id = ? AND day = ?", (user_id, day)).fetchone()
        if row is None:
            return {"requests": 0, "tokens_in": 0, "tokens_out": 0}
        return {"requests": row["requests"], "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"]}

    # -- audit ----------------------------------------------------------------
    def record_audit(self, actor: str, action: str, *,
                     target: str = "", detail: str = "") -> None:
        """Append an audit event. NEVER pass chat content or file contents."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events (ts, actor, action, target, detail)"
                " VALUES (?,?,?,?,?)",
                (_now_iso(), actor, action, target,
                 json.dumps(detail, ensure_ascii=True) if detail else ""))

    def query_audit(self, *, limit: int = 100, action: str | None = None
                    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if action:
                rows = conn.execute(
                    "SELECT * FROM audit_events WHERE action = ?"
                    " ORDER BY id DESC LIMIT ?", (action, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            if item.get("detail"):
                try:
                    item["detail"] = json.loads(item["detail"])
                except ValueError:
                    pass
            out.append(item)
        return out
