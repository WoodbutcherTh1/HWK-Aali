"""Aali Hub OpenAI-compatible /v1 surface — the external distribution entry.

Mirrors the brain's existing /v1 endpoints (file-agent/app.py, tested in
tests/test_openai_compat.py) but adds Hub concerns:

- Bearer auth: a user access JWT **or** an ``aali-…`` API key (hashed in
  users_db, resolved per request)
- Per-tier rate limits + daily quotas via the request queue
- Usage metering into users_db
- /v1/models answers from the model registry (falls back to a static entry)

Proxying: chat completions are forwarded to the brain's /v1/chat/completions
(AALI_BRAIN_URL) — streaming passes through as SSE. FastAPI imports lazily;
``set_jwt_secret`` is called by main.create_app after build.
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_openai_router"]


def build_openai_router(state: Any):  # noqa: ANN201
    """Build the /v1 router bound to a HubState (imports FastAPI lazily)."""
    try:
        from fastapi import APIRouter, Header, HTTPException, Request
        from fastapi.responses import StreamingResponse
    except ImportError as exc:  # pragma: no cover - only without fastapi
        raise RuntimeError(
            "FastAPI is required to serve the Hub (pip install fastapi "
            "uvicorn[standard])") from exc

    from aali_hub import auth as hub_auth
    from aali_hub.queue import Rejected

    router = APIRouter(prefix="/v1", tags=["openai"])
    router.jwt_secret = ""  # type: ignore[attr-defined]
    state_ref = {"state": state}

    def set_jwt_secret(secret: str) -> None:
        router.jwt_secret = secret  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # auth resolution
    # ------------------------------------------------------------------
    def _resolve_caller(authorization: str) -> dict[str, Any]:
        """Resolve the bearer to {user, role, user_id, via_key}."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401,
                                detail="missing bearer token")
        presented = authorization[len("Bearer "):].strip()
        users_db = state_ref["state"].users_db
        if presented.startswith("aali-"):
            user = users_db.resolve_api_key(presented)
            if user is None:
                raise HTTPException(status_code=401,
                                    detail="invalid API key")
            return {"user": user, "user_id": user["id"],
                    "role": user["role"], "via_key": True}
        try:
            claims = hub_auth.verify_token(presented, router.jwt_secret,
                                           kind="access")
        except hub_auth.AuthError as exc:
            raise HTTPException(status_code=401,
                                detail=f"invalid token: {exc.code}") from exc
        user = users_db.get_user(claims["sub"])
        if user is None:
            raise HTTPException(status_code=401, detail="unknown user")
        return {"user": user, "user_id": user["id"],
                "role": user["role"], "via_key": False}

    def _admit(caller: dict[str, Any]) -> None:
        """Queue admission (rate limits, quotas, circuit breaker)."""
        state = state_ref["state"]
        usage = state.users_db.usage_today(caller["user_id"])
        try:
            state.queue.admit_request(caller["user_id"], caller["role"],
                                      used_today=usage["requests"])
        except Rejected as exc:
            headers = {"Retry-After": str(exc.retry_after)} \
                if exc.retry_after else {}
            raise HTTPException(status_code=429, detail=exc.code,
                                headers=headers) from exc

    def _openai_error(status: int, message: str, err_type: str,
                      code: str) -> JSONResponse:  # noqa: F821
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=status, content={
            "error": {"message": message, "type": err_type, "code": code}})

    # ------------------------------------------------------------------
    @router.get("/models")
    def list_models(authorization: str = Header(default="")) -> Any:
        caller = _resolve_caller(authorization)
        registry = state_ref["state"].registry
        models = []
        for rec in registry.list():
            models.append({
                "id": f"{rec['model_id']}@{rec['version']}",
                "object": "model",
                "created": 0,
                "owned_by": "hwk",
                "context_length": rec["context_length"],
                "promoted": rec["promoted"],
                "uploaded_to_hf": rec["uploaded_to_hf"],
                "openrouter_listed": rec["openrouter_listed"],
            })
        if not models:
            models = [{"id": "aali", "object": "model", "created": 0,
                       "owned_by": "hwk", "context_length": 4096}]
        return {"object": "list", "data": models}

    @router.post("/chat/completions")
    def chat_completions(body: dict[str, Any] | None = None,
                         authorization: str = Header(default="")) -> Any:
        body = body or {}
        caller = _resolve_caller(authorization)
        _admit(caller)
        state = state_ref["state"]
        config = state.config
        stream = bool(body.get("stream"))
        try:
            import httpx
            payload = dict(body)
            payload.setdefault("model", "aali")
            if stream:
                def sse_iter():
                    try:
                        with httpx.stream(
                                "POST",
                                f"{config.brain_url}/v1/chat/completions",
                                json=payload,
                                headers={"X-Aali-User": caller["user_id"]},
                                timeout=config.brain_wake_timeout_sec) as resp:
                            for line in resp.iter_lines():
                                yield line + "\n"
                    finally:
                        # streaming request finished (or client went away)
                        state.queue.release_request(caller["user_id"])
                        state.users_db.record_usage(caller["user_id"],
                                                    requests=1)
                return StreamingResponse(sse_iter(),
                                         media_type="text/event-stream")
            resp = httpx.post(
                f"{config.brain_url}/v1/chat/completions",
                json=payload, headers={"X-Aali-User": caller["user_id"]},
                timeout=config.brain_wake_timeout_sec)
        except Exception as exc:
            state.queue.release_request(caller["user_id"])
            state.queue.record_brain_failure()
            return _openai_error(502, "brain unavailable",
                                 "api_error", "brain_unavailable")
        state.queue.record_brain_success()
        try:
            data = resp.json()
        except ValueError as exc:
            return _openai_error(502, "brain returned invalid JSON",
                                 "api_error", "brain_bad_response")
        finally:
            state.queue.release_request(caller["user_id"])
            state.users_db.record_usage(caller["user_id"], requests=1)
        return data

    router.set_jwt_secret = set_jwt_secret  # type: ignore[attr-defined]
    return router
