"""Server-side role resolution (Track B 11.1, 2026-09-25).

Contract
--------
Exactly five roles, resolved ONLY here from server-verified facts:

    owner  — the machine's master: AALI_API_KEY holder, or local
             single-user mode (no key configured — it is the owner's PC)
    dev    — signed-in account whose email is in AALI_DEV_EMAILS
    admin  — signed-in account whose email is in AALI_ADMIN_EMAILS
             (accounts.py also promotes these at login)
    user   — any other authenticated account/key
    guest  — remote caller that cannot authenticate (tunnel + XFF rules)

NEVER trust a client-sent role: X-Role / role fields in payloads are
ignored by design — role is computed from the master key, the issued
key store, the account session, and the connection origin ONLY.

Output of resolve_role(): {role, user_id, workspace_id, permissions}.
404-not-403 is a ROUTING concern (11.2) — this module only resolves.
"""
from __future__ import annotations

import os

# Role ranking: higher wins when several facts apply.
_ORDER = ("guest", "user", "admin", "dev", "owner")

# ————— permissions (single source of truth; 11.2/11.4/11.5 read these) —————
_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "guest": (
        "chat",
        "help",
    ),
    "user": (
        "chat",
        "own_sessions",
        "export",
        "search",
        "memory",
        "own_keys",
        "attachments",
        "profile",
        "help",
        "about",
        "tts",
    ),
    "admin": (
        "chat",
        "own_sessions",
        "export",
        "search",
        "memory",
        "own_keys",
        "attachments",
        "profile",
        "help",
        "about",
        "users",
        "workspace_settings",
        "audit_logs",
        "analytics",
        "admin_panel",
        "tts",
    ),
    "dev": (
        # dev = admin + developer surfaces
        "chat",
        "own_sessions",
        "export",
        "search",
        "memory",
        "own_keys",
        "attachments",
        "profile",
        "help",
        "about",
        "users",
        "workspace_settings",
        "audit_logs",
        "analytics",
        "admin_panel",
        "tts",
        "models",
        "logs",
        "health_full",
        "test_runner",
        "api_docs",
        "build_tools",
        "pi_ci",
        "advanced_settings",
    ),
    "owner": (
        "chat",
        "own_sessions",
        "export",
        "search",
        "memory",
        "own_keys",
        "attachments",
        "profile",
        "help",
        "about",
        "users",
        "workspace_settings",
        "audit_logs",
        "analytics",
        "admin_panel",
        "tts",
        "models",
        "logs",
        "health_full",
        "test_runner",
        "api_docs",
        "build_tools",
        "pi_ci",
        "advanced_settings",
        "training",
        "master_key",
        "rollback",
        "corpora",
        "tokenizer",
        "benchmarks",
        "security",
        "reports",
        "processes",
        "filesystem",
        "debug",
        "all_sessions",
    ),
}


def _split_emails(env_name: str) -> set[str]:
    raw = os.getenv(env_name, "") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def dev_emails() -> set[str]:
    return _split_emails("AALI_DEV_EMAILS")


def rank(role: str) -> int:
    return _ORDER.index(role) if role in _ORDER else 0


def at_least(role: str, needed: str) -> bool:
    return rank(role) >= rank(needed)


def has_permission(role: str, permission: str) -> bool:
    return permission in _PERMISSIONS.get(role, ())


def permissions_for(role: str) -> tuple[str, ...]:
    return _PERMISSIONS.get(role, ())


def resolve_role(*, api_key_configured: bool, is_master_key: bool,
                 account_email: str | None, account_is_admin: bool,
                 key_id: str | None, is_local: bool,
                 authenticated: bool) -> dict:
    """Resolve the caller's role from SERVER-VERIFIED facts only.

    Caller (app.py) supplies the facts; this module never reads request
    objects, so it stays pure and unit-testable. Resolution order:

    1. master key holder, or local single-user mode (no key configured)
       -> owner  (local + no key IS the owner's machine)
    2. account session with an admin-role email      -> admin
    3. account session with a dev email              -> dev
    4. any authenticated caller (account or issued key)
       - remote + unauthenticated                    -> guest
       - authenticated but nothing more              -> user
    """
    email = (account_email or "").strip().lower()

    if is_master_key or (not api_key_configured and is_local):
        role = "owner"
    elif account_email and account_is_admin:
        role = "admin"
    elif account_email and email in dev_emails():
        role = "dev"
    elif authenticated:
        role = "user"
    elif not is_local:
        role = "guest"
    else:
        # Local + a key IS configured but the caller presented none:
        # local tooling gets user-level read surfaces, never owner.
        role = "user" if api_key_configured else "owner"

    # user_id: stable, content-free identity for scoping/logging.
    if role == "owner":
        user_id = "owner"
    elif email:
        user_id = f"account:{email}"
    elif key_id:
        user_id = f"key:{key_id}"
    elif role == "guest":
        user_id = "guest"
    else:
        user_id = "local"

    # workspace_id: the sandbox scope this caller operates in. Owner is
    # the machine workspace; users are namespaced under it (Wave 2
    # Projects will refine this into per-workspace roots).
    if role == "owner":
        workspace_id = "hwk"
    elif role == "guest":
        workspace_id = "guest"
    else:
        workspace_id = "hwk"

    return {
        "role": role,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "permissions": list(permissions_for(role)),
    }
