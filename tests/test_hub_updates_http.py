"""HTTP-level tests for the Hub update surface (publish → serve → retire).

Runs in the hub venv (FastAPI + cryptography required); the store verifies
signatures server-side, the public routes require a bearer token, admin
publish/retire require admin/owner.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "file-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("fastapi", reason="FastAPI not installed in this venv")
pytest.importorskip("cryptography",
                    reason="cryptography not installed in this venv")

from fastapi.testclient import TestClient  # noqa: E402

from aali_hub.config import HubConfig  # noqa: E402
from aali_hub.main import create_app  # noqa: E402

SIGNING_KEY = "hub-test-signing-key-0123456789abcdef"


def _artifact(version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("aali_node/__init__.py",
                    f'__version__ = "{version}"\n')
    return buf.getvalue()


@pytest.fixture()
def hub(tmp_path: Path) -> tuple[TestClient, HubConfig]:
    config = HubConfig(
        dev_mode=True,
        jwt_secret="jwt-secret-0123456789abcdef-0123456789",
        brain_token="brain-token-0123456789abcdef-012345",
        master_key="master-key-0123456789abcdef-0123456789",
        update_signing_key=SIGNING_KEY,
        db_path=tmp_path / "hub.db",
        log_dir=tmp_path / "logs",
        update_dir=tmp_path / "updates",
        brain_url="http://127.0.0.1:1",  # dead port on purpose
    )
    app = create_app(config)
    return TestClient(app), config


@pytest.fixture()
def hub_no_updates(tmp_path: Path) -> TestClient:
    config = HubConfig(
        dev_mode=True,
        jwt_secret="jwt-secret-0123456789abcdef-0123456789",
        brain_token="brain-token-0123456789abcdef-012345",
        master_key="master-key-0123456789abcdef-0123456789",
        db_path=tmp_path / "hub.db",
        log_dir=tmp_path / "logs",
        update_dir=tmp_path / "updates",
        brain_url="http://127.0.0.1:1",
    )
    return TestClient(create_app(config))


def _seed_owner(client: TestClient) -> str:
    state = client.app.state.hub
    state.users_db.create_user("owner@hwk.local", "ownerpass123",
                               role="owner", email_verified=True)
    resp = client.post("/api/auth/login",
                       json={"email": "owner@hwk.local",
                             "password": "ownerpass123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _seed_user(client: TestClient) -> str:
    client.post("/api/auth/register",
                json={"email": "user@example.com",
                      "password": "password123"})
    resp = client.post("/api/auth/login",
                       json={"email": "user@example.com",
                             "password": "password123"})
    return resp.json()["token"]


def _publish(client: TestClient, token: str, version: str = "1.0.1",
             min_version: str = "0.0.0") -> dict:
    resp = client.post(
        f"/api/admin/updates/publish?version={version}"
        f"&min_version={min_version}",
        content=_artifact(version),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/octet-stream"})
    assert resp.status_code == 200, resp.text
    return resp.json()["update"]


# ---------------------------------------------------------------------------
# surface presence / absence
# ---------------------------------------------------------------------------
def test_updates_routes_absent_without_signing_key(hub_no_updates) -> None:
    client = hub_no_updates
    assert client.get("/updates/latest").status_code == 404
    assert client.post(
        "/api/admin/updates/publish?version=1&min_version=0",
    ).status_code == 404


def test_health_reports_update_sig_alg(hub) -> None:
    client, _ = hub
    body = client.get("/health").json()
    assert body["update_sig_alg"] == "ed25519"  # cryptography IS installed


# ---------------------------------------------------------------------------
# admin publish + gating
# ---------------------------------------------------------------------------
def test_publish_requires_owner(hub) -> None:
    client, _ = hub
    user_tok = _seed_user(client)
    assert client.post(
        "/api/admin/updates/publish?version=1.0.1&min_version=0",
        content=b"x",
        headers={"Authorization": f"Bearer {user_tok}"},
    ).status_code == 403
    assert client.post(
        "/api/admin/updates/publish?version=1.0.1&min_version=0",
        content=b"x",
    ).status_code == 401


def test_publish_and_list_round_trip(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    signed = _publish(client, tok)
    assert signed["sig_alg"] in ("ed25519", "hmac-sha256")
    assert signed["sha256"]
    resp = client.get("/api/admin/updates",
                      headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 200
    assert resp.json()["versions"] == ["1.0.1"]


def test_publish_rejects_empty_body_and_bad_version(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    hdrs = {"Authorization": f"Bearer {tok}"}
    assert client.post(
        "/api/admin/updates/publish?version=1.0.1&min_version=0",
        content=b"", headers=hdrs).status_code == 400
    assert client.post(
        "/api/admin/updates/publish?version=../evil&min_version=0",
        content=b"x", headers=hdrs).status_code == 400


# ---------------------------------------------------------------------------
# public surface: manifest + download (signature-verified server-side)
# ---------------------------------------------------------------------------
def test_latest_and_manifest_require_token(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    _publish(client, tok)
    assert client.get("/updates/latest").status_code == 401
    assert client.get("/updates/1.0.1/manifest").status_code == 401
    assert client.get("/updates/1.0.1/download").status_code == 401


def test_latest_manifest_download_round_trip(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    _publish(client, tok)
    user_tok = _seed_user(client)
    hdrs = {"Authorization": f"Bearer {user_tok}"}  # free users CAN update

    latest = client.get("/updates/latest", headers=hdrs)
    assert latest.status_code == 200
    manifest = latest.json()["update"]
    assert manifest["version"] == "1.0.1"
    assert manifest["url"] == "/updates/1.0.1/download"

    one = client.get("/updates/1.0.1/manifest", headers=hdrs)
    assert one.status_code == 200
    assert one.json()["update"]["sha256"] == manifest["sha256"]

    dl = client.get("/updates/1.0.1/download", headers=hdrs)
    assert dl.status_code == 200
    assert dl.content == _artifact("1.0.1")
    assert dl.headers["content-type"] == "application/zip"


def test_latest_404_when_nothing_published(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    resp = client.get("/updates/latest",
                      headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 404


def test_manifest_and_download_404_for_unknown_version(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    _publish(client, tok)
    user_tok = _seed_user(client)
    hdrs = {"Authorization": f"Bearer {user_tok}"}
    assert client.get("/updates/9.9.9/manifest",
                      headers=hdrs).status_code == 404
    assert client.get("/updates/9.9.9/download",
                      headers=hdrs).status_code == 404


# ---------------------------------------------------------------------------
# retire
# ---------------------------------------------------------------------------
def test_retire_removes_version(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    _publish(client, tok, "1.0.1")
    _publish(client, tok, "1.0.2")
    hdrs = {"Authorization": f"Bearer {tok}"}
    assert client.delete("/api/admin/updates/1.0.1",
                         headers=hdrs).status_code == 200
    assert client.get("/api/admin/updates",
                      headers=hdrs).json()["versions"] == ["1.0.2"]
    user_tok = _seed_user(client)
    assert client.get("/updates/1.0.1/manifest",
                      headers={"Authorization": f"Bearer {user_tok}"}
                      ).status_code == 404


def test_retire_refuses_last_remaining_version(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    _publish(client, tok)
    resp = client.delete("/api/admin/updates/1.0.1",
                         headers={"Authorization": f"Bearer {tok}"})
    # surface returns 404 for both unknown + refused, but the detail must
    # name the refusal and the version must SURVIVE (still served)
    assert resp.status_code == 404
    assert "refusing" in resp.json()["detail"]
    hdrs = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/admin/updates",
                      headers=hdrs).json()["versions"] == ["1.0.1"]


def test_retire_requires_admin(hub) -> None:
    client, _ = hub
    tok = _seed_owner(client)
    _publish(client, tok)
    user_tok = _seed_user(client)
    assert client.delete("/api/admin/updates/1.0.1",
                         headers={"Authorization": f"Bearer {user_tok}"}
                         ).status_code == 403
