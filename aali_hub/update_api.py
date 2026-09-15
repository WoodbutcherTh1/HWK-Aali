"""Aali Hub update API — the HTTP surface over aali_hub.update_server.

Public (any authenticated user):
    GET /updates/latest                     → newest valid manifest
    GET /updates/{version}/manifest         → one valid manifest
    GET /updates/{version}/download         → artifact bytes (sha-checked)

Admin (role admin/owner) — content enters the store ONLY through here:
    GET    /api/admin/updates               → published versions
    POST   /api/admin/updates/publish       → publish one release (raw bytes
             body; manifest fields as query params, manifest signed here)
    DELETE /api/admin/updates/{version}     → retire one version (rollback window)

Security model (matches update_server.py):
- manifests are signature-verified before they leave the store;
- artifacts are sha256-verified against their manifest before download;
- admin actions are JWT-gated (role admin/owner) and audited, actor = subject;
- the Node never trusts the Hub's word: it re-verifies signature + sha256
  client-side (aali_node/updater.py).
"""

from __future__ import annotations

from typing import Any

from aali_hub.update_server import UpdateError, UpdateStore

# PEP 563 lesson (see AGENTS.md): annotations resolve against MODULE globals.
# Request must be importable at module level or `request: Request` degrades
# to a query parameter (422 on every publish).
try:
    from fastapi import Request
except ImportError:  # pragma: no cover - only without fastapi
    Request = Any  # type: ignore[misc,assignment]

__all__ = ["build_update_router", "build_update_admin_router"]


def _raise_http(status: int, detail: str):  # noqa: ANN201
    """Translate to HTTPException (caller imports fastapi already)."""
    from fastapi import HTTPException
    raise HTTPException(status_code=status, detail=detail)


def build_update_router(store: UpdateStore):  # noqa: ANN201
    """Build the public /updates router (imports FastAPI lazily)."""
    try:
        from fastapi import APIRouter, Header, Response
    except ImportError as exc:  # pragma: no cover - only without fastapi
        raise RuntimeError(
            "FastAPI is required to serve the Hub (pip install fastapi "
            "uvicorn[standard])") from exc

    router = APIRouter(prefix="/updates", tags=["updates"])
    jwt_box: dict[str, str] = {"secret": ""}

    def set_jwt_secret(secret: str) -> None:
        jwt_box["secret"] = secret

    def _require_user(authorization: str = Header(default="")) -> dict[str, Any]:
        from aali_hub import auth as hub_auth
        if not authorization.startswith("Bearer "):
            _raise_http(401, "missing bearer token")
        try:
            return hub_auth.verify_token(authorization[len("Bearer "):],
                                         jwt_box["secret"], kind="access")
        except Exception as exc:
            _raise_http(401, f"invalid token: {exc}")

    @router.get("/latest")
    def latest(authorization: str = Header(default="")) -> dict:
        _require_user(authorization)
        try:
            manifest = store.latest_manifest()
        except UpdateError as exc:
            _raise_http(404, str(exc))
        return {"ok": True, "update": manifest}

    @router.get("/{version}/manifest")
    def manifest(version: str,
                 authorization: str = Header(default="")) -> dict:
        _require_user(authorization)
        try:
            return {"ok": True, "update": store.manifest_for(version)}
        except UpdateError as exc:
            _raise_http(404, str(exc))

    @router.get("/{version}/download")
    def download(version: str,
                 authorization: str = Header(default="")) -> Response:
        _require_user(authorization)
        try:
            data = store.artifact_bytes(version)
        except UpdateError as exc:
            _raise_http(404, str(exc))
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition":
                                 f'attachment; filename="aali-node-{version}.zip"'})

    router.set_jwt_secret = set_jwt_secret  # type: ignore[attr-defined]
    return router


def build_update_admin_router(store: UpdateStore, users_db: Any,
                              audit_log: Any):  # noqa: ANN201
    """Build the admin publish/retire router (imports FastAPI lazily)."""
    try:
        from fastapi import APIRouter, Depends, Header
    except ImportError as exc:  # pragma: no cover - only without fastapi
        raise RuntimeError(
            "FastAPI is required to serve the Hub (pip install fastapi "
            "uvicorn[standard])") from exc

    router = APIRouter(prefix="/api/admin/updates", tags=["updates"])
    jwt_box: dict[str, str] = {"secret": ""}

    def set_jwt_secret(secret: str) -> None:
        jwt_box["secret"] = secret

    def require_admin(authorization: str = Header(default="")) -> dict[str, Any]:
        from aali_hub import auth as hub_auth
        if not authorization.startswith("Bearer "):
            _raise_http(401, "missing bearer token")
        try:
            claims = hub_auth.verify_token(authorization[len("Bearer "):],
                                           jwt_box["secret"], kind="access")
        except Exception as exc:
            _raise_http(401, f"invalid token: {exc}")
        if claims.get("role") not in ("admin", "owner"):
            _raise_http(403, "admin role required")
        return claims

    @router.get("")
    def list_versions(claims: dict = Depends(require_admin)) -> dict:
        return {"ok": True, "versions": store.list_versions()}

    @router.post("/publish")
    async def publish(request: Request,
                      version: str,
                      min_version: str,
                      claims: dict = Depends(require_admin)) -> dict:
        # Raw-bytes body (no multipart parser dependency): the admin tool
        # streams the artifact; manifest fields arrive as query params.
        artifact = await request.body()
        if not artifact:
            _raise_http(400, "empty artifact body")
        try:
            signed = store.publish(
                version, min_version, artifact,
                release_notes_ar=request.query_params.get(
                    "release_notes_ar", ""),
                release_notes_en=request.query_params.get(
                    "release_notes_en", ""))
        except UpdateError as exc:
            _raise_http(400, str(exc))
        users_db.record_audit(claims["sub"], "admin_publish_update",
                              target=version)
        audit_log.log_tool_call(claims["sub"], "update_publish",
                                {"version": version}, "ok")
        return {"ok": True, "update": signed}

    @router.delete("/{version}")
    def retire(version: str,
               claims: dict = Depends(require_admin)) -> dict:
        try:
            store.retire(version)
        except UpdateError as exc:
            _raise_http(404, str(exc))
        users_db.record_audit(claims["sub"], "admin_retire_update",
                              target=version)
        return {"ok": True}

    router.set_jwt_secret = set_jwt_secret  # type: ignore[attr-defined]
    return router
