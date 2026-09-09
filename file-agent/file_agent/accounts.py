"""Aali accounts — users | builders & team (admin, owner).

One app, two worlds: regular users sign up with email + password, verify with
a code, reset their password, and chat. The owner/builders sign in with an
email listed in the ADMINS list and get the SAME app plus admin features
(/admin dashboard, /brain tree, admin APIs). Admins are users with a role —
they are NOT a separate app.

Design follows file_agent/apikeys.py: stdlib-only, JSONL store, secrets hashed
(PBKDF2-SHA256 for passwords, SHA-256 for session tokens and codes), loaded
into memory, writes rewrite the file. An account links to the apikeys platform
by carrying its own key_id (issued on verification), so sessions/keys metering
keep working unchanged.

Store: file-agent/accounts.jsonl — {email, pw_hash, salt, role, verified,
key_id, created_at, last_login}. NEVER the plaintext password.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "accounts.jsonl"

# The builders/owner list. Emails here sign in as admin (role promoted at
# login). Env override so deployments don't need a code edit.
ADMIN_EMAILS_ENV = "AALI_ADMIN_EMAILS"
DEFAULT_ADMINS = {"hmam@hwk.team"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CODE_TTL = 15 * 60  # verification / reset codes live 15 minutes
_SESSION_TTL = 30 * 24 * 3600  # 30 days
_PBKDF2_ITERS = 120_000

_lock = threading.Lock()
_accounts: dict[str, dict] = {}  # email(lower) -> record
_pending: dict[str, dict] = {}  # email -> {"code_hash", "purpose", "expires", "pw_hash", "salt"}
_sessions: dict[str, dict] = {}  # token_hash -> {"email", "expires"}
_store_path: Path = DEFAULT_STORE


def _configure(path: str | Path | None = None) -> None:
    global _store_path
    if path is not None:
        _store_path = Path(path)


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
                _accounts[str(rec["email"]).lower()] = rec
    except (json.JSONDecodeError, KeyError, OSError):
        pass


def _save() -> None:
    try:
        _store_path.parent.mkdir(parents=True, exist_ok=True)
        with _store_path.open("w", encoding="utf-8") as handle:
            for rec in _accounts.values():
                handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def ensure_loaded() -> None:
    with _lock:
        if not _accounts:
            _load()


def _now() -> float:
    return time.time()


def admin_emails() -> set[str]:
    raw = os.getenv(ADMIN_EMAILS_ENV, "")
    emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return emails | DEFAULT_ADMINS if not raw else emails


def _is_admin_email(email: str) -> bool:
    return email.lower() in admin_emails()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERS
    ).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def password_problem(password: str) -> str | None:
    """Return an Arabic error string, or None when acceptable (min 8 chars)."""
    if len(password or "") < 8:
        return "كلمة السر يجب أن تكون 8 أحرف على الأقل"
    return None


def signup(email: str, password: str, ip: str = "") -> dict:
    """Register: stores the account unverified and issues a 6-digit code.
    Returns {ok, dev_code?} — dev_code only when AALI_AUTH_DEBUG=1 (no mailer
    wired yet; the owner gets codes via the server log until then)."""
    email = _normalize(email)
    if not valid_email(email):
        return {"ok": False, "error": "بريد إلكتروني غير صالح"}
    problem = password_problem(password)
    if problem:
        return {"ok": False, "error": problem}
    with _lock:
        if not _accounts and not _pending:
            _load()
        if email in _accounts:
            return {"ok": False, "error": "هذا البريد مسجّل بالفعل — سجّل دخولك أو أعد تعيين كلمة السر"}
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(16)
        _pending[email] = {
            "code_hash": _token_hash(code),
            "purpose": "verify",
            "expires": _now() + _CODE_TTL,
            "pw_hash": _hash_password(password, salt),
            "salt": salt,
            "ip": ip,
        }
    role = "admin" if _is_admin_email(email) else "user"
    return {
        "ok": True,
        "message": "أدخل رمز التحقق المرسل إلى بريدك",
        # No SMTP yet: surface the code in the server log for the owner.
        "dev_code": code if os.getenv("AALI_AUTH_DEBUG", "1") == "1" else None,
        "role": role,
    }


def verify(email: str, code: str) -> dict:
    """Confirm the 6-digit code, create the account, issue its API key."""
    email = _normalize(email)
    with _lock:
        pend = _pending.get(email)
        if not pend or pend["purpose"] != "verify":
            return {"ok": False, "error": "لا يوجد تسجيل قيد الانتظار لهذا البريد"}
        if pend["expires"] < _now():
            del _pending[email]
            return {"ok": False, "error": "انتهت صلاحية الرمز — سجّل من جديد"}
        if not secrets.compare_digest(pend["code_hash"], _token_hash((code or "").strip())):
            return {"ok": False, "error": "رمز خاطئ"}
        del _pending[email]
        role = "admin" if _is_admin_email(email) else "user"
        rec = {
            "email": email,
            "pw_hash": pend["pw_hash"],
            "salt": pend["salt"],
            "role": role,
            "verified": True,
            "key_id": None,  # linked below by the server (apikeys integration)
            "created_at": _now(),
            "last_login": _now(),
        }
        _accounts[email] = rec
        _save()
    return {"ok": True, "email": email, "role": role}


def login(email: str, password: str) -> dict:
    """Password sign-in. Admin emails get role=admin even if stored as user
    (the list is the source of truth). Returns a session token."""
    email = _normalize(email)
    with _lock:
        if not _accounts:
            _load()
        rec = _accounts.get(email)
        if not rec or not rec.get("verified"):
            return {"ok": False, "error": "بريد أو كلمة سر غير صحيحة"}
        if not secrets.compare_digest(rec["pw_hash"], _hash_password(password, rec["salt"])):
            return {"ok": False, "error": "بريد أو كلمة سر غير صحيحة"}
        rec["last_login"] = _now()
        if _is_admin_email(email):
            rec["role"] = "admin"
        else:
            rec["role"] = "user"
        role = rec["role"]
        _save()
        token = secrets.token_urlsafe(32)
        _sessions[_token_hash(token)] = {
            "email": email,
            "expires": _now() + _SESSION_TTL,
        }
    return {"ok": True, "token": token, "email": email, "role": role}


def reset_request(email: str) -> dict:
    """Issue a password-reset code (same 6-digit shape)."""
    email = _normalize(email)
    with _lock:
        if not _accounts:
            _load()
        if email not in _accounts:
            # Do not leak which emails exist.
            return {"ok": True, "message": "إن كان البريد مسجلاً فسيصل رمز لإعادة التعيين"}
        code = f"{secrets.randbelow(1_000_000):06d}"
        _pending[email] = {
            "code_hash": _token_hash(code),
            "purpose": "reset",
            "expires": _now() + _CODE_TTL,
        }
    return {
        "ok": True,
        "message": "رمز إعادة التعيين أُرسل إلى بريدك",
        "dev_code": code if os.getenv("AALI_AUTH_DEBUG", "1") == "1" else None,
    }


def reset_confirm(email: str, code: str, new_password: str) -> dict:
    email = _normalize(email)
    problem = password_problem(new_password)
    if problem:
        return {"ok": False, "error": problem}
    with _lock:
        if not _accounts:
            _load()
        pend = _pending.get(email)
        if not pend or pend["purpose"] != "reset":
            return {"ok": False, "error": "اطلب رمز إعادة التعيين أولاً"}
        if pend["expires"] < _now():
            del _pending[email]
            return {"ok": False, "error": "انتهت صلاحية الرمز — اطلب رمزاً جديداً"}
        if not secrets.compare_digest(pend["code_hash"], _token_hash((code or "").strip())):
            return {"ok": False, "error": "رمز خاطئ"}
        del _pending[email]
        rec = _accounts.get(email)
        if not rec:
            return {"ok": False, "error": "بريد غير مسجّل"}
        rec["salt"] = secrets.token_hex(16)
        rec["pw_hash"] = _hash_password(new_password, rec["salt"])
        _sessions.clear()  # force re-login everywhere after a reset
        _save()
    return {"ok": True, "message": "تم تعيين كلمة سر جديدة — سجّل دخولك"}


def session(token: str) -> dict | None:
    """Resolve a session token -> {email, role, is_admin} or None."""
    if not token:
        return None
    with _lock:
        if not _accounts:
            _load()
        sess = _sessions.get(_token_hash(token))
        if not sess or sess["expires"] < _now():
            return None
        rec = _accounts.get(sess["email"])
        if not rec:
            return None
        return {
            "email": rec["email"],
            "role": rec.get("role", "user"),
            "is_admin": rec.get("role") == "admin",
            "key_id": rec.get("key_id"),
        }


def logout(token: str) -> None:
    with _lock:
        _sessions.pop(_token_hash(token), None)


def link_key(email: str, key_id: str) -> None:
    """Attach the account to its issued API key (server-side, on verification)."""
    email = _normalize(email)
    with _lock:
        rec = _accounts.get(email)
        if rec:
            rec["key_id"] = key_id
            _save()


def account(email: str) -> dict | None:
    with _lock:
        if not _accounts:
            _load()
        rec = _accounts.get(_normalize(email))
        return dict(rec) if rec else None


def remove_account(email: str) -> bool:
    """Admin action: delete an account (and its sessions)."""
    email = _normalize(email)
    with _lock:
        if not _accounts:
            _load()
        if email not in _accounts:
            return False
        del _accounts[email]
        _save()
        dead = {h for h, s in _sessions.items() if s["email"] == email}
        for h in dead:
            _sessions.pop(h, None)
    return True


def list_accounts() -> list[dict]:
    """Admin view: accounts without password material."""
    with _lock:
        if not _accounts:
            _load()
        return [
            {
                "email": r["email"],
                "role": r.get("role", "user"),
                "verified": r.get("verified", False),
                "key_id": r.get("key_id"),
                "created_at": r.get("created_at"),
                "last_login": r.get("last_login"),
            }
            for r in _accounts.values()
        ]
