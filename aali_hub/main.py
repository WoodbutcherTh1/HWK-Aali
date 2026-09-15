"""Aali Hub main — FastAPI app factory wiring every router together.

Mounts: /health, /metrics, /api/admin/*, /api/auth/*, /v1/* (OpenAI surface),
/ws/brain, /ws/node. The heavy per-route logic lives in sibling modules;
this file is wiring + small request handlers with tests.
"""

from __future__ import annotations

from typing import Any

from aali_hub import admin_api, audit as audit_mod
from aali_hub import model_registry, users_db
from aali_hub.config import HubConfig, load_config
from aali_hub.queue import Rejected, RequestQueue
from aali_hub.update_server import HAS_ED25519, UpdateStore
from aali_hub.ws_gateway import Broker, ConnectionManager, build_router

__all__ = ["create_app", "HubState"]

# optional fastapi import — hub tests can run pure-logic modules without it
try:
    from fastapi import FastAPI, HTTPException, Request, Response
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover - only without fastapi
    _HAS_FASTAPI = False


class HubState:
    """Container for the app's shared singletons."""

    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self.users_db = users_db.UsersDB(config.db_path)
        self.registry = model_registry.ModelRegistry(config.db_path)
        self.audit_log = audit_mod.HubAudit(
            config.log_dir / "hub_tool_audit.jsonl")
        self.queue = RequestQueue(config)
        self.manager = ConnectionManager()
        self.broker = Broker(self.manager, jwt_secret=config.jwt_secret,
                             brain_token=config.brain_token)
        # Update store: active only when a signing key is configured —
        # update_server (HMAC mode) refuses an empty key by design, and an
        # unsigned update surface must simply not exist. Ed25519 is used
        # automatically when the `cryptography` package is installed.
        self.updates: UpdateStore | None = None
        if config.update_signing_key:
            self.updates = UpdateStore(
                config.update_dir, config.update_signing_key,
                keep_versions=config.update_keep_versions)


def create_app(config: HubConfig | None = None):  # noqa: ANN201
    """Build the FastAPI app. Raises RuntimeError without FastAPI."""
    if not _HAS_FASTAPI:
        raise RuntimeError(
            "FastAPI is required to serve the Hub (pip install fastapi "
            "uvicorn[standard])")
    from fastapi import FastAPI, HTTPException, Request, Response
    from fastapi.responses import JSONResponse

    config = config or load_config()
    state = HubState(config)
    app = FastAPI(title="Aali Hub", version="0.1.0",
                  docs_url="/docs", openapi_url="/openapi.json")
    app.state.hub = state

    # ---- basic routers --------------------------------------------------
    admin_router = admin_api.build_admin_router(
        state.users_db, state.registry, state.audit_log)
    app.include_router(admin_router)

    from aali_hub import openai_compat
    openai_router = openai_compat.build_openai_router(state)
    app.include_router(openai_router)

    app.include_router(build_router(state.broker, state.manager))
    # supply the JWT secret to the routers that gate on it
    admin_router.set_jwt_secret(config.jwt_secret)
    openai_router.set_jwt_secret(config.jwt_secret)

    # ---- updates (signed Node auto-update surface) -----------------------
    if state.updates is not None:
        from aali_hub import update_api
        update_router = update_api.build_update_router(state.updates)
        update_admin_router = update_api.build_update_admin_router(
            state.updates, state.users_db, state.audit_log)
        app.include_router(update_router)
        app.include_router(update_admin_router)
        update_router.set_jwt_secret(config.jwt_secret)          # type: ignore[attr-defined]
        update_admin_router.set_jwt_secret(config.jwt_secret)    # type: ignore[attr-defined]

    # ---- health / metrics ------------------------------------------------
    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "service": "aali-hub",
            "version": "0.1.0",
            "update_sig_alg":
                "ed25519" if (state.updates is not None and HAS_ED25519)
                else ("hmac-sha256" if state.updates is not None else ""),
            "brain_connected": state.manager.brain() is not None,
            "nodes_connected": state.manager.live_count("node"),
            "queue_depth": state.queue.queue_depth(),
            "circuit_open": not state.queue.brain_available(),
        }

    @app.get("/metrics")
    def metrics() -> Response:
        lines = [
            "aali_hub_up 1",
            f"aali_hub_nodes_connected {state.manager.live_count('node')}",
            f"aali_hub_brain_connected "
            f"{1 if state.manager.brain() is not None else 0}",
            f"aali_hub_queue_depth {state.queue.queue_depth()}",
            f"aali_hub_circuit_open "
            f"{0 if state.queue.brain_available() else 1}",
            f"aali_hub_dispatched {state.broker.stats['dispatched']}",
            f"aali_hub_relayed {state.broker.stats['relayed']}",
            f"aali_hub_inbound_dropped "
            f"{state.broker.stats['inbound_dropped']}",
        ]
        body = "\n".join(lines) + "\n"
        return Response(content=body, media_type="text/plain")

    # ---- auth endpoints --------------------------------------------------
    from fastapi import Header

    def _bearer_claims(authorization: str,
                       secret: str) -> dict[str, Any]:
        from aali_hub import auth as hub_auth
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401,
                                detail="missing bearer token")
        try:
            return hub_auth.verify_token(authorization[len("Bearer "):],
                                         secret, kind="access")
        except hub_auth.AuthError as exc:
            raise HTTPException(status_code=401,
                                detail=f"invalid token: {exc.code}") from exc

    @app.post("/api/auth/register")
    def register(body: dict[str, Any] | None = None) -> dict:
        body = body or {}
        try:
            user = state.users_db.create_user(
                body.get("email", ""), body.get("password", ""),
                role="free_user")
        except users_db.UserError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        state.users_db.record_audit("system", "register", target=user["id"])
        return {"ok": True, "user": user}

    @app.post("/api/auth/login")
    def login(body: dict[str, Any] | None = None) -> dict:
        body = body or {}
        user = state.users_db.verify_login(body.get("email", ""),
                                           body.get("password", ""))
        if user is None:
            raise HTTPException(status_code=401,
                                detail="invalid credentials")
        from aali_hub import auth as hub_auth
        token = hub_auth.mint_token(
            user["id"], user["role"], config.jwt_secret,
            ttl_sec=config.access_token_ttl_sec, kind="access")
        state.users_db.record_audit(user["id"], "login")
        return {"ok": True, "token": token, "user": user}

    @app.get("/api/auth/me")
    def me(authorization: str = Header(default="")) -> dict:
        claims = _bearer_claims(authorization, config.jwt_secret)
        user = state.users_db.get_user(claims["sub"])
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        return {"ok": True, "user": user,
                "usage": state.users_db.usage_today(claims["sub"])}

    # ---- brain chat proxy (queue + metering + circuit breaker) -----------
    @app.post("/api/ask")
    def ask(body: dict[str, Any] | None = None,
            authorization: str = Header(default="")) -> dict:
        body = body or {}
        claims = _bearer_claims(authorization, config.jwt_secret)
        user = state.users_db.get_user(claims["sub"])
        if user is None or user.get("disabled"):
            raise HTTPException(status_code=403, detail="account disabled")
        usage = state.users_db.usage_today(claims["sub"])
        try:
            state.queue.admit_request(claims["sub"], user["role"],
                                      used_today=usage["requests"])
        except Rejected as exc:
            headers = {"Retry-After": str(exc.retry_after)} \
                if exc.retry_after else {}
            raise HTTPException(status_code=429, detail=exc.code,
                                headers=headers) from exc
        try:
            import httpx
            resp = httpx.post(
                f"{config.brain_url}/api/ask",
                json={"message": body.get("message", ""),
                      "sid": body.get("sid", ""),
                      "user_id": claims["sub"]},
                headers={"X-Aali-User": claims["sub"]},
                timeout=config.brain_wake_timeout_sec)
            state.queue.record_brain_success()
        except Exception as exc:
            state.queue.record_brain_failure()
            raise HTTPException(status_code=502,
                                detail="brain unavailable") from exc
        finally:
            state.queue.release_request(claims["sub"])
        state.users_db.record_usage(claims["sub"], requests=1)
        data = resp.json()
        return {"ok": True, "reply": data.get("reply", ""),
                "sid": data.get("sid", "")}

    return app


def main() -> None:  # pragma: no cover - manual run helper
    """Run the Hub locally: ``python -m aali_hub.main``."""
    import uvicorn

    config = load_config()
    uvicorn.run(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":  # pragma: no cover
    main()
