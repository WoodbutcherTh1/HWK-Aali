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

from aali_hub.app_updates import AppUpdateError
from aali_hub.update_server import UpdateError, UpdateStore

# PEP 563 lesson (see AGENTS.md): annotations resolve against MODULE globals.
# Request must be importable at module level or `request: Request` degrades
# to a query parameter (422 on every publish).
try:
    from fastapi import Request
except ImportError:  # pragma: no cover - only without fastapi
    Request = Any  # type: ignore[misc,assignment]

__all__ = ["build_update_router", "build_update_admin_router",
           "build_app_update_router", "APPS", "PLATFORMS"]

#: The three self-updating clients (Studio, Desktop, CLI). The Aali Node is NOT
#: here: it has its own single-artifact store below, and it keeps working.
APPS = ("studio", "desktop", "cli")
PLATFORMS = ("win", "macos", "linux")


def _raise_http(status: int, detail: str):  # noqa: ANN201
    """Translate to HTTPException (caller imports fastapi already)."""
    from fastapi import HTTPException
    raise HTTPException(status_code=status, detail=detail)


def build_update_router(store: UpdateStore):  # noqa: ANN201
    """Build the public /updates router for the Aali NODE (imports FastAPI lazily).

    Unchanged contract, unchanged auth: these routes require a bearer token
    because the Node is a logged-in device. The per-app routes below are
    public instead — a client that can update itself before anyone logs into
    it has no user to log in as yet.
    """
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


def build_app_update_router(store: Any):  # noqa: ANN201
    """The per-app, per-platform update surface (studio · desktop · cli).

    PUBLIC on purpose: these are signed, and the signature is the
    authentication. A client that must be able to update BEFORE anyone logs
    into it has no token to present, and the artifacts (a .exe / .app / a
    one-file CLI) are not secrets — anyone may download them, nobody may forge
    them. Publishing, listing and retiring stay admin-only.

        GET /updates/{app}/latest?platform=&channel=
        GET /updates/{app}/{version}/manifest
        GET /updates/{app}/{version}/download/{platform}

    Unknown app / version / platform answers 404, never 403: a wrong path
    should not confirm that the path exists in another shape.
    """
    try:
        from fastapi import APIRouter, Response
    except ImportError as exc:  # pragma: no cover - only without fastapi
        raise RuntimeError(
            "FastAPI is required to serve the Hub (pip install fastapi "
            "uvicorn[standard])") from exc

    router = APIRouter(prefix="/updates", tags=["updates"])

    def _fail_app(exc: Exception):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=str(exc))

    @router.get("/{app}/latest")
    def app_latest(app: str, platform: str = "", channel: str = "") -> dict:
        app = str(app).lower()
        if app not in APPS:
            _fail_app(AppUpdateError(f"unknown app: {app!r}"))
        try:
            manifest = store.latest(app, platform=platform, channel=channel)
        except AppUpdateError as exc:
            _fail_app(exc)
        combined = None
        try:
            combined = store.version_manifest(app, str(manifest["version"]))
        except AppUpdateError:
            combined = None  # a single-platform release still serves fine
        return {
            "ok": True,
            "app": app,
            "version": manifest.get("version"),
            "platform": manifest.get("platform"),
            "channel": manifest.get("channel"),
            "released": manifest.get("released"),
            "notes": {
                "ar": manifest.get("release_notes_ar", ""),
                "en": manifest.get("release_notes_en", ""),
            },
            "platforms": (combined or {}).get("platforms", {}),
            # the per-platform signed manifest the clients verify
            "update": manifest,
        }

    @router.get("/{app}/{version}/manifest")
    def app_manifest(app: str, version: str) -> dict:
        app = str(app).lower()
        if app not in APPS:
            _fail_app(AppUpdateError(f"unknown app: {app!r}"))
        try:
            return {"ok": True, "update": store.version_manifest(app, version)}
        except AppUpdateError as exc:
            _fail_app(exc)

    @router.get("/{app}/{version}/download/{platform}")
    def app_download(app: str, version: str, platform: str) -> Response:
        app = str(app).lower()
        platform = str(platform).lower()
        if app not in APPS or platform not in PLATFORMS:
            _fail_app(AppUpdateError(
                f"unknown app/platform: {app!r}/{platform!r}"))
        try:
            data = store.artifact_bytes(app, version, platform)
        except AppUpdateError as exc:
            _fail_app(exc)
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition":
                                 f'attachment; filename="aali-{app}-'
                                 f'{version}-{platform}.zip"'})

    return router


def build_app_update_admin_router(store: Any, users_db: Any,
                                  audit_log: Any):  # noqa: ANN201
    """DEPRECATED SHIM — kept so an old deployment does not crash on import.

    The per-app admin endpoints live on the main router now
    (``build_update_admin_router(..., app_store=…)``), which is where the
    owner's publish tooling posts to. Do not wire this one up.
    """
    raise RuntimeError(
        "build_app_update_admin_router was folded into "
        "build_update_admin_router(store, users_db, audit_log, app_store=...)")


def build_update_admin_router(store: UpdateStore, users_db: Any,
                              audit_log: Any, app_store: Any = None):  # noqa: ANN201
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
                      min_version: str = "",
                      app: str = "",
                      claims: dict = Depends(require_admin)) -> dict:
        # ONE publish endpoint, two shapes. Without `app` this is the Aali
        # Node's original single-artifact release (unchanged). With
        # `app=studio|desktop|cli` it is the per-app store, which REQUIRES a
        # signed artifact (`sig`) and a platform: an unsigned client artifact
        # never enters that store. Two shapes on one path beats two admin
        # contracts that drift apart.
        artifact = await request.body()
        if not artifact:
            _raise_http(400, "empty artifact body")
        if app:
            if app_store is None:
                _raise_http(400, "the per-app update store is not enabled")
            params = request.query_params
            try:
                signed = app_store.publish(
                    app, version, params.get("platform", ""), artifact,
                    sha256=params.get("sha256", ""),
                    signature=params.get("sig", ""),
                    channel=params.get("channel", "stable"),
                    min_version=min_version or params.get("min_version", ""),
                    notes_ar=params.get("release_notes_ar", ""),
                    notes_en=params.get("release_notes_en", ""),
                    filename=params.get("filename", ""))
            except AppUpdateError as exc:
                _raise_http(400, str(exc))
            users_db.record_audit(claims["sub"], "admin_publish_app_update",
                                  target=f"{app}/{version}/"
                                         f"{params.get('platform', '')}")
            audit_log.log_tool_call(claims["sub"], "app_update_publish",
                                    {"app": app, "version": version,
                                     "platform": params.get("platform", "")},
                                    "ok")
            return {"ok": True, "update": signed}
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

    # ---- per-app (studio / desktop / cli) ---------------------------------
    # Registered AFTER the Node routes on purpose: FastAPI matches in order,
    # and `/api/admin/updates/{version}` must keep winning for a bare version.

    @router.get("/list")
    def list_app_versions(app: str = "",
                          claims: dict = Depends(require_admin)) -> dict:
        if app_store is None:
            _raise_http(400, "the per-app update store is not enabled")
        if app:
            return {"ok": True, "app": app,
                    "versions": app_store.list_versions(app)}
        return {"ok": True, "apps": app_store.list_apps()}

    @router.delete("/{app}/{version}")
    def retire_app(app: str, version: str,
                   claims: dict = Depends(require_admin)) -> dict:
        if app_store is None:
            _raise_http(400, "the per-app update store is not enabled")
        try:
            app_store.retire(app, version)
        except AppUpdateError as exc:
            # retiring the LAST version is a policy refusal (400), not a
            # lookup miss (404) — the admin needs to know which one it was.
            _raise_http(400 if "last remaining" in str(exc) else 404, str(exc))
        users_db.record_audit(claims["sub"], "admin_retire_app_update",
                              target=f"{app}/{version}")
        return {"ok": True}

    router.set_jwt_secret = set_jwt_secret  # type: ignore[attr-defined]
    return router
