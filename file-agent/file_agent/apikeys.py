"""Aali API-key platform — Aali is the provider and issues his own keys.

A user key is a high-entropy token ``aali-<hex>``. Only its SHA-256 hash is
stored, so a leaked store never leaks a usable key. The master ``AALI_API_KEY``
(the admin key) is NOT stored here; it is read from the environment and checked
separately. Each issued key has its own ``key_id`` used for session isolation
and usage metering, plus a display ``prefix``.

Storage: JSONL at ``file-agent/api_keys.jsonl``, one record per key, keyed by
``key_id``. Loaded once into memory; writes rewrite the whole file (the store
is tiny). This module is dependency-free (stdlib only) so it is safe to import
from the server, the CLI, or tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "api_keys.jsonl"

# Self-serve signup guard: how many new keys a single IP may mint per day.
DEFAULT_SIGNUP_PER_DAY = 5
# Optional per-key daily request cap (0 = unlimited).
KEY_DAILY_CAP_ENV = "AALI_KEY_DAILY_CAP"

_lock = threading.Lock()
_keys: dict[str, dict] = {}
_store_path: Path = DEFAULT_STORE


def _configure(path: str | Path | None = None) -> None:
    """Point the store at a different file (used by tests / CLI)."""
    global _store_path
    if path is not None:
        _store_path = Path(path)


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _load() -> None:
    if not _store_path.exists():
        return
    try:
        with _store_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                _keys[rec["key_id"]] = rec
    except (json.JSONDecodeError, KeyError, OSError):
        pass


def _save() -> None:
    try:
        _store_path.parent.mkdir(parents=True, exist_ok=True)
        with _store_path.open("w", encoding="utf-8") as handle:
            for rec in _keys.values():
                handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _now() -> float:
    return time.time()


def _signed_up_today(ip: str) -> int:
    """How many keys this IP has already signed up for today (0 = none)."""
    day = time.strftime("%Y-%m-%d")
    return sum(
        1 for rec in _keys.values()
        if rec.get("created_by_ip") == ip
        and time.strftime("%Y-%m-%d", time.localtime(float(rec.get("created_at", 0)))) == day
    )


def issue_key(label: str = "", created_by: str = "", ip: str = "") -> tuple[str, str]:
    """Create a new key and return ``(key_id, plaintext)``.

    ``created_by`` is the admin key id (or ``"admin"``) for admin-issued keys,
    or ``"signup"`` for self-serve. ``ip`` is used for the signup rate guard.
    """
    plaintext = "aali-" + secrets.token_hex(24)
    key_id = secrets.token_hex(12)
    with _lock:
        _keys[key_id] = {
            "key_id": key_id,
            "key_hash": _hash(plaintext),
            "prefix": plaintext[:12],
            "label": (label or "").strip(),
            "created_at": _now(),
            "created_by": created_by,
            "created_by_ip": ip,
        "revoked": False,
        "last_used_at": 0.0,
        "requests": 0,
        "chars_in": 0,
        "chars_out": 0,
        "cap_day": "",
        "cap_requests": 0,
    }
        _save()
    return key_id, plaintext


def authenticate(plaintext: str) -> dict | None:
    """Return the record for a valid, non-revoked key, else None."""
    if not plaintext:
        return None
    h = _hash(plaintext)
    for rec in _keys.values():
        if rec.get("key_hash") == h:
            if rec.get("revoked"):
                return None
            return rec
    return None


def get(key_id: str) -> dict | None:
    return _keys.get(key_id)


def revoke(key_id: str) -> bool:
    with _lock:
        rec = _keys.get(key_id)
        if not rec:
            return False
        rec["revoked"] = True
        _save()
        return True


def list_keys() -> list[dict]:
    """Return non-secret summaries (no hashes, no plaintext)."""
    out = []
    for rec in _keys.values():
        out.append({
            "key_id": rec["key_id"],
            "prefix": rec["prefix"],
            "label": rec.get("label", ""),
            "created_at": rec.get("created_at", 0),
            "created_by": rec.get("created_by", ""),
            "revoked": bool(rec.get("revoked")),
            "last_used_at": rec.get("last_used_at", 0),
            "requests": rec.get("requests", 0),
            "chars_in": rec.get("chars_in", 0),
            "chars_out": rec.get("chars_out", 0),
        })
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def stats() -> dict:
    keys = list_keys()
    active = sum(1 for k in keys if not k["revoked"])
    total_requests = sum(k["requests"] for k in keys)
    total_chars = sum(k["chars_in"] for k in keys) + sum(k["chars_out"] for k in keys)
    return {
        "total_keys": len(keys),
        "active_keys": active,
        "revoked_keys": len(keys) - active,
        "total_requests": total_requests,
        "total_chars": total_chars,
    }


def record_usage(key_id: str) -> None:
    """Bump a key's request counter + last-used (best-effort, never raises)."""
    rec = _keys.get(key_id)
    if not rec:
        return
    day = time.strftime("%Y-%m-%d")
    rec["requests"] = int(rec.get("requests", 0)) + 1
    rec["last_used_at"] = _now()
    if rec.get("cap_day") != day:
        rec["cap_day"] = day
        rec["cap_requests"] = 1
    else:
        rec["cap_requests"] = int(rec.get("cap_requests", 0)) + 1
    _save()


def record_chars(key_id: str, chars_in: int, chars_out: int) -> None:
    """Add char totals only (no request count) — used by compute endpoints."""
    rec = _keys.get(key_id)
    if not rec:
        return
    rec["chars_in"] = int(rec.get("chars_in", 0)) + int(chars_in)
    rec["chars_out"] = int(rec.get("chars_out", 0)) + int(chars_out)
    _save()


def over_daily_cap(key_id: str) -> bool:
    """True when this key already used its daily request cap (0 = unlimited)."""
    cap = int(os.getenv(KEY_DAILY_CAP_ENV, "0") or 0)
    if cap <= 0:
        return False
    day = time.strftime("%Y-%m-%d")
    rec = _keys.get(key_id)
    if not rec:
        return False
    if rec.get("cap_day") != day:
        return False
    return int(rec.get("cap_requests", 0)) >= cap


def signup(label: str, ip: str) -> tuple[str, str, str]:
    """Self-serve signup, rate-limited per IP. Returns (key_id, plaintext, error)."""
    if os.getenv("AALI_OPEN_SIGNUP", "1").strip().lower() in ("0", "false", "off", "no"):
        return "", "", "signup is disabled by the administrator; keys are issued from the admin dashboard"
    per_day = int(os.getenv("AALI_SIGNUP_PER_DAY", str(DEFAULT_SIGNUP_PER_DAY)))
    if _signed_up_today(ip) >= per_day:
        return "", "", f"signup rate limit reached ({per_day}/day per IP); use the admin dashboard"
    key_id, plaintext = issue_key(label=label, created_by="signup", ip=ip)
    return key_id, plaintext, ""


def ensure_loaded() -> None:
    if not _keys:
        _load()
