"""Per-user execution context for Aali Cloud SaaS mode (STEP 4).

Shared by the brain (file-agent/app.py) and testable in any venv. Identity
model: the Hub authenticates every user and forwards a *verified, non-empty*
``X-Aali-User`` header (a UUID minted by the Hub — not an email, never a
client-supplied value). The brain re-derives nothing from the header except
scope:

- ``user_root(user_id)`` → ``AALI_USERS_DIR/<user_id>/`` — the per-user
  workspace/memory sandbox root (env ``AALI_USERS_DIR`` overrides the
  ``~/.aali/users`` default so tests stay hermetic).
- ``aali_mode()`` → ``"local"`` | ``"saas"`` from ``AALI_MODE``. Local mode
  remains the default and never consults any of this.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

__all__ = ["SaasModeError", "aali_mode", "validate_user_id", "user_root",
           "user_memory_dir"]

_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")

_FORBIDDEN_USER_ID = (".", "..", "workspace", "uploads", "users")


class SaasModeError(Exception):
    """Raised when a user context is invalid or mode config is broken."""


def aali_mode(env: dict[str, str] | None = None) -> str:
    """Return the active mode: ``"local"`` (default) or ``"saas"``."""
    source = os.environ if env is None else env
    mode = (source.get("AALI_MODE") or "local").strip().lower()
    if mode not in ("local", "saas"):
        raise SaasModeError(
            f"AALI_MODE must be 'local' or 'saas', got {mode!r}")
    return mode


def validate_user_id(user_id: str) -> str:
    """Validate a Hub-provided user id (strict allow-list).

    Rejects everything that could escape the sandbox: path separators,
    traversal dots, reserved names, overlong ids. The id never carries
    meaning by itself — it is an opaque scope key.
    """
    uid = (user_id or "").strip()
    if not _USER_ID_RE.match(uid):
        raise SaasModeError("invalid user id")
    if uid.lower() in _FORBIDDEN_USER_ID:
        raise SaasModeError("reserved user id")
    return uid


def users_base_dir(env: dict[str, str] | None = None) -> Path:
    """Base directory holding every per-user namespace."""
    source = os.environ if env is None else env
    override = (source.get("AALI_USERS_DIR") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".aali" / "users"


def user_root(user_id: str, env: dict[str, str] | None = None) -> Path:
    """The per-user sandbox root (workspace + memory live under it)."""
    uid = validate_user_id(user_id)
    return users_base_dir(env) / uid


def user_memory_dir(user_id: str, env: dict[str, str] | None = None) -> Path:
    """The per-user memory directory (memory.py honours AALI_MEMORY_DIR)."""
    return user_root(user_id, env) / "memory"
