"""Owner preview mode — "View as user" (Track B 11.7, 2026-09-25).

The owner can flip THEIR OWN session into a preview that resolves to the
`user` role, to check exactly what a normal signed-in user sees — without
logging out, without a second browser, and without any way for a CLIENT
to turn it on or off.

Security contract
-----------------
- The flag lives in the account session store (server-side), keyed by the
  session token hash — a client header/field can never set or clear it.
- Only OWNER-grade callers may start a preview (master key, or a session
  that ALREADY resolves to owner/admin/dev). A guest or plain user gets
  the uniform 404 — the endpoint does not exist for them.
- Ending a preview requires a valid session too; the response never
  reveals whether a preview was active (idempotent clear).
- The effective role while previewing is ALWAYS "user" — never lower
  (a guest cannot preview anything) and never higher. Permissions come
  from the roles table, so previewing changes ONLY the role verdict.

Content-free audit events: preview_start / preview_stop (actor + ip,
never payloads) via file_agent.audit.
"""
from __future__ import annotations

import time

from file_agent import accounts, roles as roles_mod

# Session-store key for the preview flag (server-side only).
_FLAG = "preview_as_user"

# Master-key/local-owner preview: their requests carry NO session token,
# so the flag rides in a process-global bounded timer instead (owner-only;
# dies with the process; default window 30 min, owner can stop early).
_MASTER_UNTIL = 0.0
_MASTER_WINDOW = 30 * 60


def start(session_token: str) -> dict | None:
    """Enable preview for THIS session. Returns the refreshed session
    summary or None when the token is invalid (or role < admin — those
    callers have no preview surface at all)."""
    sess = accounts.session(session_token)
    if not sess:
        return None
    effective = roles_mod.resolve_role(
        api_key_configured=True,
        is_master_key=False,
        account_email=sess["email"],
        account_is_admin=bool(sess["is_admin"]),
        key_id=sess.get("key_id"),
        is_local=True,
        authenticated=True,
    )
    if effective["role"] not in ("owner", "admin", "dev"):
        return None
    accounts.set_session_flag(session_token, _FLAG, True)
    return summary(session_token)


def stop(session_token: str) -> dict | None:
    """Disable preview for THIS session (idempotent; invalid token -> None)."""
    if not accounts.session(session_token):
        return None
    accounts.set_session_flag(session_token, _FLAG, False)
    return summary(session_token)


def master_start(seconds: int | None = None) -> dict:
    """Arm master-key preview for a bounded window (owner-only callers;
    the route enforces the role, this just tracks the timer)."""
    global _MASTER_UNTIL
    _MASTER_UNTIL = time.time() + max(60, min(int(seconds or _MASTER_WINDOW),
                                              24 * 3600))
    return {"previewing": True, "ttl": int(_MASTER_UNTIL - time.time())}


def master_stop() -> None:
    global _MASTER_UNTIL
    _MASTER_UNTIL = 0.0


def master_active() -> bool:
    return _MASTER_UNTIL > time.time()


def active(session_token: str) -> bool:
    """True when THIS session is currently previewing as a user."""
    if not session_token:
        return False
    sess = accounts.session(session_token)
    return bool(sess and (sess.get("flags") or {}).get(_FLAG))


def summary(session_token: str) -> dict | None:
    """The session after the flag change: {email, previewing} — content-free."""
    sess = accounts.session(session_token)
    if not sess:
        return None
    return {"email": sess["email"],
            "previewing": bool((sess.get("flags") or {}).get(_FLAG))}
