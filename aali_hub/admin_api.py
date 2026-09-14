"""Aali Hub admin API — owner/admin-only management endpoints.

Gating: requests must carry ``Authorization: Bearer <access JWT>`` whose
claims resolve to role ``admin`` or ``owner``. Every mutating action is
appended to the users_db audit trail (actor = subject user id). No chat
content, no secrets in responses — password hashes and key material never
leave the store.

Bodies are plain dicts validated manually (not Pydantic models): the module
keeps FastAPI/pydantic imports lazy so the package stays importable in the
training venv and on Pi CI. (Pydantic models defined inside this factory
cannot be resolved by FastAPI under PEP 563 annotations.)
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["build_admin_router", "AdminError"]


class AdminError(Exception):
    """Admin endpoint error with an HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


def build_admin_router(users_db: Any, registry: Any,
                       audit_log: Any):  # noqa: ANN201
    """Build the admin router (imports FastAPI lazily)."""
    try:
        from fastapi import APIRouter, Depends, Header, HTTPException
    except ImportError as exc:  # pragma: no cover - only without fastapi
        raise RuntimeError(
            "FastAPI is required to serve the Hub (pip install fastapi "
            "uvicorn[standard])") from exc

    router = APIRouter(prefix="/api/admin", tags=["admin"])
    state_box: dict[str, str] = {"jwt_secret": ""}

    def require_admin(authorization: str = Header(default="")) -> dict[str, Any]:
        """Resolve the bearer JWT and enforce the admin/owner role."""
        from aali_hub import auth as hub_auth
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = authorization[len("Bearer "):]
        try:
            claims = hub_auth.verify_token(token, state_box["jwt_secret"],
                                           kind="access")
        except Exception as exc:
            raise HTTPException(status_code=401,
                                detail=f"invalid token: {exc}") from exc
        if claims.get("role") not in ("admin", "owner"):
            raise HTTPException(status_code=403, detail="admin role required")
        return claims

    def set_jwt_secret(secret: str) -> None:
        state_box["jwt_secret"] = secret

    def _bad(detail: str) -> HTTPException:
        return HTTPException(status_code=400, detail=detail)

    # -- users -------------------------------------------------------------
    @router.get("/users")
    def list_users(claims: dict = Depends(require_admin)) -> dict:
        return {"ok": True, "users": users_db_list(users_db)}

    @router.post("/users/{user_id}/role")
    def set_role(user_id: str, body: dict | None = None,
                 claims: dict = Depends(require_admin)) -> dict:
        payload = body or {}
        try:
            user = users_db.set_role(user_id, str(payload.get("role", "")))
        except Exception as exc:
            raise _bad(str(exc)) from exc
        users_db.record_audit(claims["sub"], "admin_set_role",
                              target=user_id, detail=str(user["role"]))
        return {"ok": True, "user": user}

    @router.post("/users/{user_id}/disable")
    def set_disabled(user_id: str, body: dict | None = None,
                     claims: dict = Depends(require_admin)) -> dict:
        payload = body or {}
        if not isinstance(payload.get("disabled"), bool):
            raise _bad("disabled must be a boolean")
        try:
            user = users_db.set_disabled(user_id, payload["disabled"])
        except Exception as exc:
            raise _bad(str(exc)) from exc
        users_db.record_audit(claims["sub"], "admin_disable",
                              target=user_id, detail=str(payload["disabled"]))
        return {"ok": True, "user": user}

    # -- usage / audit -------------------------------------------------------
    @router.get("/usage")
    def usage(claims: dict = Depends(require_admin)) -> dict:
        return {"ok": True, "usage": users_db_usage_all(users_db)}

    @router.get("/audit")
    def audit(limit: int = 100, action: str | None = None,
              claims: dict = Depends(require_admin)) -> dict:
        return {"ok": True,
                "events": users_db.query_audit(limit=limit, action=action)}

    # -- model registry --------------------------------------------------------
    @router.get("/models")
    def models(claims: dict = Depends(require_admin)) -> dict:
        return {"ok": True, "models": registry.list()}

    @router.post("/models")
    def add_model(body: dict | None = None,
                  claims: dict = Depends(require_admin)) -> dict:
        payload = body or {}
        try:
            record = registry.register(
                str(payload.get("model_id", "")),
                str(payload.get("version", "")),
                str(payload.get("path", "")),
                str(payload.get("format", "")),
                context_length=int(payload.get("context_length", 4096)),
                tokenizer=str(payload.get("tokenizer", "")),
                promoted=bool(payload.get("promoted", False)),
                eval_score=payload.get("eval_score"))
        except Exception as exc:
            raise _bad(str(exc)) from exc
        users_db.record_audit(claims["sub"], "admin_register_model",
                              target=f"{payload.get('model_id')}"
                                     f"@{payload.get('version')}")
        return {"ok": True, "model": record}

    @router.post("/models/{model_id}/{version}/promote")
    def promote(model_id: str, version: str,
                claims: dict = Depends(require_admin)) -> dict:
        try:
            record = registry.mark_promoted(model_id, version)
        except Exception as exc:
            raise _bad(str(exc)) from exc
        users_db.record_audit(claims["sub"], "admin_promote_model",
                              target=f"{model_id}@{version}")
        return {"ok": True, "model": record}

    @router.delete("/models/{model_id}/{version}")
    def remove_model(model_id: str, version: str,
                     claims: dict = Depends(require_admin)) -> dict:
        try:
            registry.remove(model_id, version)
        except Exception as exc:
            raise _bad(str(exc)) from exc
        users_db.record_audit(claims["sub"], "admin_remove_model",
                              target=f"{model_id}@{version}")
        return {"ok": True}

    router.set_jwt_secret = set_jwt_secret  # type: ignore[attr-defined]
    return router


# ---------------------------------------------------------------------------
# small helpers kept outside the router for testability
# ---------------------------------------------------------------------------
def users_db_list(users_db: Any) -> list[dict[str, Any]]:
    """List all users (public fields) — direct SQL on the store."""
    import sqlite3
    conn = sqlite3.connect(users_db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, email, role, created_at, disabled, email_verified"
            " FROM users ORDER BY created_at").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def users_db_usage_all(users_db: Any) -> list[dict[str, Any]]:
    """Per-user usage totals across all days."""
    import sqlite3
    conn = sqlite3.connect(users_db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT user_id, SUM(requests) AS requests, SUM(tokens_in)"
            " AS tokens_in, SUM(tokens_out) AS tokens_out"
            " FROM usage GROUP BY user_id").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
