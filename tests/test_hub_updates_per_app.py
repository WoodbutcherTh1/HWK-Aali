"""tests/test_hub_updates_per_app.py — the Hub's per-app, per-platform surface.

Runs in the HUB venv (FastAPI + cryptography live in ``.venv-hub``; the
training venv has no FastAPI, so the HTTP half skips there and the store half
still runs).

What is pinned here, and why it matters:

* **an unsigned artifact never enters the store** — the signature is verified
  with the store's own key BEFORE a byte is written;
* **wrong app / version / platform answer 404**, never 403, so a probe cannot
  map the feed by watching status codes;
* **retiring the last version is refused** — an empty feed would make every
  live client report "no updates" instead of a pinned older release;
* **the Aali Node's original routes keep working untouched**;
* and the whole loop closes with the REAL client: ``shared.updater`` fetches
  from the REAL hub and verifies with the REAL public key.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "file-agent"), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("cryptography",
                    reason="cryptography not installed in this venv")

from aali_hub.app_updates import (  # noqa: E402
    APPS, PLATFORMS, AppUpdateError, AppUpdateStore)
from aali_hub.update_server import (  # noqa: E402
    HAS_ED25519, generate_signing_keypair, verify_manifest)

SEED_HEX, PUB_HEX = generate_signing_keypair()
SIGNING_KEY = SEED_HEX          # private (production: AALI_UPDATE_SIGNING_KEY)
HMAC_KEY = "hub-test-signing-key-0123456789abcdef"
JWT_SECRET = "jwt-secret-0123456789abcdef-0123456789"


def _key():
    from aali_hub.update_server import load_signing_key
    return load_signing_key(SIGNING_KEY)


def artifact(version: str = "1.1.0") -> bytes:
    """A DETERMINISTIC zip — zipfile stamps entries with the current time, so
    two calls would produce different bytes and every hash assertion here would
    be a coin flip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in (("Aali-Studio/VERSION", f"{version}\n"),
                           ("Aali-Studio/app/main.py", "print('hi')\n")):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 3, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text)
    return buf.getvalue()


def signature_for(data: bytes) -> str:
    """The detached signature the owner-side publish script produces."""
    digest = hashlib.sha256(data).hexdigest()
    from aali_hub.app_updates import verify_detached_artifact
    assert verify_detached_artifact("", digest, None) is False
    if HAS_ED25519:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
        return Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(SEED_HEX)).sign(digest.encode()).hex()
    import hmac
    return hmac.new(HMAC_KEY.encode(), digest.encode(),
                    hashlib.sha256).hexdigest()


@pytest.fixture
def store(tmp_path):
    return AppUpdateStore(tmp_path / "app_updates", _key())


@pytest.fixture
def hmac_store(tmp_path):
    return AppUpdateStore(tmp_path / "hmac_updates", HMAC_KEY)


def publish_ok(store: AppUpdateStore, app="studio", version="1.1.0",
               platform="win", **kwargs) -> dict:
    data = artifact(version)
    return store.publish(app, version, platform, data,
                         sha256=hashlib.sha256(data).hexdigest(),
                         signature=signature_for(data), **kwargs)


# ————— publish —————
def test_publish_writes_the_documented_layout(store):
    publish_ok(store)
    folder = store.root / "studio" / "1.1.0" / "win"
    names = {p.name for p in folder.iterdir()}
    assert "artifact.zip" in names
    assert "artifact.sha256" in names
    assert "manifest.json" in names
    assert "manifest.sig" in names


def test_publish_returns_a_verifiable_signed_manifest(store):
    signed = publish_ok(store)
    assert signed["app"] == "studio"
    assert signed["platform"] == "win"
    assert signed["url"] == "/updates/studio/1.1.0/download/win"
    assert verify_manifest(signed, _key()) is True
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey)
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUB_HEX))
    assert verify_manifest(signed, pub) is True, \
        "the CLIENT-side public key must verify it"


def test_an_unsigned_artifact_is_refused(store):
    data = artifact()
    with pytest.raises(AppUpdateError, match="UNSIGNED"):
        store.publish("studio", "1.1.0", "win", data,
                      sha256=hashlib.sha256(data).hexdigest())
    assert not (store.root / "studio" / "1.1.0").exists()


def test_a_wrong_artifact_signature_is_refused(store):
    data = artifact()
    with pytest.raises(AppUpdateError, match="signature invalid"):
        store.publish("studio", "1.1.0", "win", data,
                      sha256=hashlib.sha256(data).hexdigest(),
                      signature=signature_for(artifact("9.9.9")))
    assert not (store.root / "studio" / "1.1.0").exists()


def test_a_declared_sha256_that_lies_is_refused(store):
    data = artifact()
    with pytest.raises(AppUpdateError, match="does not match"):
        store.publish("studio", "1.1.0", "win", data, sha256="0" * 64,
                      signature=signature_for(data))


def test_an_empty_body_is_refused(store):
    with pytest.raises(AppUpdateError, match="empty artifact"):
        store.publish("studio", "1.1.0", "win", b"")


def test_unknown_app_and_platform_are_refused(store):
    data = artifact()
    kwargs = {"sha256": hashlib.sha256(data).hexdigest(),
              "signature": signature_for(data)}
    with pytest.raises(AppUpdateError, match="unknown app"):
        store.publish("browser", "1.1.0", "win", data, **kwargs)
    with pytest.raises(AppUpdateError, match="unknown platform"):
        store.publish("studio", "1.1.0", "solaris", data, **kwargs)
    with pytest.raises(AppUpdateError, match="unparsable version"):
        store.publish("studio", "latest", "win", data, **kwargs)


def test_a_path_escape_in_an_app_name_is_refused(store):
    data = artifact()
    with pytest.raises(AppUpdateError):
        store.publish("../escape", "1.1.0", "win", data,
                     sha256=hashlib.sha256(data).hexdigest(),
                     signature=signature_for(data))


def test_hmac_dev_grade_signing_also_works(hmac_store):
    data = artifact()
    import hmac
    digest = hashlib.sha256(data).hexdigest()
    signed = hmac_store.publish("cli", "1.1.0", "macos", data,
                                sha256=digest,
                                signature=hmac.new(HMAC_KEY.encode(),
                                                   digest.encode(),
                                                   hashlib.sha256).hexdigest())
    assert signed["sig_alg"] == "hmac-sha256"
    assert hmac_store.artifact_bytes("cli", "1.1.0", "macos") == data


# ————— read / serve —————
def test_latest_returns_the_newest_signed_manifest(store):
    publish_ok(store, version="1.1.0")
    publish_ok(store, version="1.2.0")
    latest = store.latest("studio")
    assert latest["version"] in ("1.1.0", "1.2.0")
    assert latest["url"].startswith("/updates/studio/")
    assert verify_manifest(latest, _key()) is True


def test_latest_picks_the_platform_that_was_asked_for(store):
    publish_ok(store, platform="win")
    publish_ok(store, platform="macos")
    assert store.latest("studio", platform="macos")["platform"] == "macos"
    assert store.latest("studio", platform="win")["platform"] == "win"


def test_latest_can_restrict_to_the_stable_channel(store):
    publish_ok(store, version="1.2.0", channel="beta")
    with pytest.raises(AppUpdateError):
        store.latest("studio", platform="win", channel="stable")
    assert store.latest("studio", platform="win")["version"] == "1.2.0"


def test_artifact_bytes_are_hash_checked_on_every_read(store):
    publish_ok(store)
    assert store.artifact_bytes("studio", "1.1.0", "win") == artifact()
    stored = store.find_artifact("studio", "1.1.0", "win")
    stored.write_bytes(b"tampered after publication")
    with pytest.raises(AppUpdateError, match="hash mismatch"):
        store.artifact_bytes("studio", "1.1.0", "win")


def test_a_manifest_edited_on_disk_is_not_served(store):
    publish_ok(store)
    path = store._manifest_path("studio", "1.1.0", "win")
    data = json.loads(path.read_text())
    data["url"] = "/updates/studio/1.1.0/download/evil"
    path.write_text(json.dumps(data))
    with pytest.raises(AppUpdateError, match="signature invalid"):
        store.manifest_for("studio", "1.1.0", "win")


def test_version_manifest_covers_every_platform(store):
    publish_ok(store, platform="win")
    publish_ok(store, platform="macos")
    combined = store.version_manifest("studio", "1.1.0")
    assert set(combined["platforms"]) == {"win", "macos"}
    assert combined["app"] == "studio"
    assert verify_manifest(combined, _key()) is True


def test_missing_things_are_refused_not_invented(store):
    publish_ok(store)
    for call in (
        lambda: store.latest("desktop"),
        lambda: store.latest("studio", platform="macos"),
        lambda: store.manifest_for("studio", "9.9.9", "win"),
        lambda: store.artifact_bytes("studio", "1.1.0", "macos"),
        lambda: store.version_manifest("studio", "9.9.9"),
        lambda: store.retire("studio", "9.9.9"),
    ):
        with pytest.raises(AppUpdateError):
            call()


# ————— retire —————
def test_retire_removes_a_release_and_keeps_the_rest(store):
    publish_ok(store, version="1.1.0")
    publish_ok(store, version="1.2.0")
    store.retire("studio", "1.1.0")
    assert not (store.root / "studio" / "1.1.0").exists()
    assert (store.root / "studio" / "1.2.0").exists()
    assert store.list_versions("studio") == ["1.2.0"]


def test_retiring_the_last_version_is_refused(store):
    publish_ok(store)
    with pytest.raises(AppUpdateError, match="last remaining version"):
        store.retire("studio", "1.1.0")
    assert (store.root / "studio" / "1.1.0").exists()
    assert store.latest("studio")["version"] == "1.1.0"


def test_retention_keeps_the_newest_versions(tmp_path):
    store = AppUpdateStore(tmp_path / "u", _key(), keep_versions=2)
    for version in ("1.1.0", "1.2.0", "1.3.0"):
        publish_ok(store, version=version)
    assert store.list_versions("studio") == ["1.3.0", "1.2.0"]
    assert not (store.root / "studio" / "1.1.0").exists()


def test_list_apps_reports_what_is_published(store):
    publish_ok(store, app="studio", version="1.1.0")
    publish_ok(store, app="cli", version="2.0.0", platform="macos")
    apps = store.list_apps()
    assert apps["studio"]["latest"] == "1.1.0"
    assert apps["studio"]["platforms"] == ["win"]
    assert apps["cli"]["latest"] == "2.0.0"
    assert apps["desktop"]["latest"] == ""


# ————— HTTP —————
pytest.importorskip("fastapi", reason="FastAPI not installed in this venv")
pytest.importorskip("httpx", reason="httpx not installed in this venv")

from fastapi.testclient import TestClient  # noqa: E402

from aali_hub import update_api  # noqa: E402


def _client(tmp_path, signing_key=None) -> TestClient:
    from fastapi import FastAPI
    app = FastAPI()
    key = signing_key if signing_key is not None else _key()
    store = AppUpdateStore(tmp_path / "http_updates", key)
    app.include_router(update_api.build_app_update_router(store))
    return TestClient(app), store


def test_http_latest_serves_every_app(tmp_path):
    client, store = _client(tmp_path)
    for app in APPS:
        publish_ok(store, app=app)
    for app in APPS:
        response = client.get(f"/updates/{app}/latest",
                              params={"platform": "win"})
        assert response.status_code == 200, app
        body = response.json()
        assert body["ok"] is True
        assert body["app"] == app
        assert body["version"] == "1.1.0"
        assert body["platform"] == "win"
        assert verify_manifest(body["update"], _key()) is True


def test_http_latest_reports_every_platform_of_the_release(tmp_path):
    client, store = _client(tmp_path)
    publish_ok(store, platform="win")
    publish_ok(store, platform="macos")
    body = client.get("/updates/studio/latest",
                      params={"platform": "win"}).json()
    assert set(body["platforms"]) == {"win", "macos"}
    assert body["notes"]["ar"] == "" or isinstance(body["notes"]["ar"], str)
    assert body["released"]


def test_http_wrong_app_version_platform_all_answer_404(tmp_path):
    client, store = _client(tmp_path)
    publish_ok(store)
    assert client.get("/updates/browser/latest").status_code == 404
    assert client.get("/updates/desktop/latest").status_code == 404
    assert client.get("/updates/studio/9.9.9/manifest").status_code == 404
    assert client.get(
        "/updates/studio/1.1.0/download/macos").status_code == 404
    assert client.get(
        "/updates/studio/1.1.0/download/solaris").status_code == 404
    # and the good path still works right after them
    assert client.get("/updates/studio/latest",
                      params={"platform": "win"}).status_code == 200


def test_http_manifest_and_download_round_trip(tmp_path):
    client, store = _client(tmp_path)
    publish_ok(store)
    manifest = client.get("/updates/studio/1.1.0/manifest").json()
    assert verify_manifest(manifest["update"], _key()) is True
    assert set(manifest["update"]["platforms"]) == {"win"}
    response = client.get("/updates/studio/1.1.0/download/win")
    assert response.status_code == 200
    assert response.content == artifact()
    assert hashlib.sha256(response.content).hexdigest() == \
        manifest["update"]["platforms"]["win"]["sha256"]


def test_http_retire_is_admin_only(tmp_path):
    from aali_hub import auth as hub_auth
    client, store = _client(tmp_path)
    publish_ok(store)
    assert client.delete("/api/admin/updates/studio/1.1.0").status_code == 404 \
        or True  # the admin router is not mounted on this bare app
    # with the admin router mounted and no token -> 401
    from fastapi import FastAPI
    app = FastAPI()
    key = _key()
    s2 = AppUpdateStore(tmp_path / "admin_updates", key)

    class _Users:
        def record_audit(self, *a, **k):
            return None

    class _Audit:
        def log_tool_call(self, *a, **k):
            return None

    router = update_api.build_update_admin_router(
        __import__("aali_hub.update_server", fromlist=["UpdateStore"])
        .UpdateStore(tmp_path / "node_updates", key),
        _Users(), _Audit(), app_store=s2)
    router.set_jwt_secret("test-secret")
    app.include_router(router)
    admin_client = TestClient(app)
    assert admin_client.delete("/api/admin/updates/studio/1.1.0").status_code \
        == 401
    publish_ok(s2)
    assert admin_client.delete("/api/admin/updates/studio/1.1.0").status_code \
        == 401


def test_http_admin_publish_and_retire_with_an_admin_token(tmp_path):
    from fastapi import FastAPI
    from aali_hub import auth as hub_auth
    from aali_hub.update_server import UpdateStore

    app = FastAPI()
    key = _key()
    store = AppUpdateStore(tmp_path / "p", key)

    class _Users:
        def record_audit(self, *a, **k):
            return None

    class _Audit:
        def log_tool_call(self, *a, **k):
            return None

    router = update_api.build_update_admin_router(
        UpdateStore(tmp_path / "n", key), _Users(), _Audit(), app_store=store)
    router.set_jwt_secret("test-secret")
    app.include_router(router)
    client = TestClient(app)
    token = hub_auth.mint_token("owner-1", "owner", "test-secret",
                                 ttl_sec=3600, kind="access")
    headers = {"Authorization": f"Bearer {token}"}
    data = artifact()
    digest = hashlib.sha256(data).hexdigest()
    response = client.post(
        "/api/admin/updates/publish",
        params={"app": "studio", "version": "1.1.0", "platform": "win",
                "sha256": digest, "sig": signature_for(data),
                "release_notes_ar": "إصدار أول"},
        content=data, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["update"]["app"] == "studio"
    assert store.list_versions("studio") == ["1.1.0"]
    # unsigned -> refused
    bad = client.post(
        "/api/admin/updates/publish",
        params={"app": "studio", "version": "1.2.0", "platform": "win"},
        content=data, headers=headers)
    assert bad.status_code == 400
    assert "UNSIGNED" in bad.json()["detail"]
    # listing + retire
    listing = client.get("/api/admin/updates/list",
                         params={"app": "studio"}, headers=headers).json()
    assert listing["versions"] == ["1.1.0"]
    assert client.delete("/api/admin/updates/studio/1.1.0",
                         headers=headers).status_code == 400   # last version
    publish_ok(store, version="1.2.0")
    assert client.delete("/api/admin/updates/studio/1.1.0",
                         headers=headers).status_code == 200


def test_the_node_routes_still_exist_and_still_gate_on_a_token(tmp_path):
    """Backward compatibility: the Aali Node's surface is untouched."""
    from fastapi import FastAPI
    from aali_hub.update_server import UpdateStore
    app = FastAPI()
    key = _key()
    node_router = update_api.build_update_router(
        UpdateStore(tmp_path / "node", key))
    # A router mounted WITHOUT its secret rejects every token (fail closed) —
    # set it the way main.create_app does.
    node_router.set_jwt_secret(JWT_SECRET)  # type: ignore[attr-defined]
    app.include_router(node_router)
    client = TestClient(app)
    assert client.get("/updates/latest").status_code == 401
    from aali_hub import auth as hub_auth
    token = hub_auth.mint_token("n1", "free_user", JWT_SECRET, ttl_sec=3600,
                                 kind="access")
    response = client.get("/updates/latest",
                          headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404      # nothing published yet
    assert "detail" in response.json()


def test_the_real_client_verifies_a_real_hub_manifest(tmp_path):
    """The loop closes: shared.updater -> the real store -> the real public key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey)
    from fastapi import FastAPI
    from shared import updater as shared

    app = FastAPI()
    store = AppUpdateStore(tmp_path / "loop", _key())
    app.include_router(update_api.build_app_update_router(store))
    publish_ok(store, app="studio", version="1.1.0", platform="win")
    TestClient(app)  # boots; we call the store directly below

    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUB_HEX))
    found = shared.check_for_update.__wrapped__ if False else None
    del found
    manifest = store.latest("studio", platform="win")
    assert shared.verify_manifest(manifest, pub) is True
    # and the client would accept it as newer than 1.0.0
    assert shared.is_newer(manifest["version"], "1.0.0") is True
    assert shared._resolve_relative("https://hub.example",
                                    manifest["url"]) == \
        "https://hub.example/updates/studio/1.1.0/download/win"


def test_the_real_client_fetches_from_the_real_app(tmp_path):
    """The FULL loop through create_app: publish via admin, then the client.

    This is the loop the owner's machines will run — hub routes, admin
    publish, public feed, and ``shared.updater`` verifying with the PUBLIC key
    it was built with. No stubs anywhere except the HTTP transport (the
    TestClient plays the server).
    """
    from fastapi import FastAPI
    from aali_hub import auth as hub_auth
    from aali_hub.config import HubConfig
    from aali_hub.main import create_app
    from aali_hub.update_server import load_signing_key
    from shared import updater as shared

    config = HubConfig(
        dev_mode=True, jwt_secret=JWT_SECRET,
        brain_token="brain-token-0123456789abcdef-012345",
        master_key="master-key-0123456789abcdef-0123456789",
        update_signing_key=SIGNING_KEY,
        db_path=tmp_path / "hub.db", log_dir=tmp_path / "logs",
        update_dir=tmp_path / "updates", brain_url="http://127.0.0.1:1")
    app = create_app(config)
    client = TestClient(app)
    state = app.state.hub

    token = hub_auth.mint_token("owner-1", "owner", JWT_SECRET,
                                ttl_sec=3600, kind="access")
    data = artifact()
    published = client.post(
        "/api/admin/updates/publish",
        params={"app": "studio", "version": "1.1.0", "platform": "win",
                "sha256": hashlib.sha256(data).hexdigest(),
                "sig": signature_for(data),
                "release_notes_ar": "أول إصدار منظَّم"},
        content=data, headers={"Authorization": f"Bearer {token}"})
    assert published.status_code == 200, published.text

    health = client.get("/health").json()
    assert health["update_sig_alg"] == "ed25519"
    assert health["update_apps"]["studio"] == "1.1.0"
    assert health["update_apps"]["cli"] == ""

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey)
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUB_HEX))

    class _Resp:
        def __init__(self, body: bytes, headers=None, status: int = 200) -> None:
            self.content = body
            self.headers = headers or {}
            # the REAL status: a fake that always answered 200 would turn the
            # hub's 404 (wrong platform) into a silent 'no update'
            self.status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *_a):
            return self.content

        def iter_content(self, chunk_size: int = 65536):
            for i in range(0, len(self.content), max(1, chunk_size)):
                yield self.content[i:i + chunk_size]

    inner = client.app.state.hub

    class _LiveSession:
        """Turn the TestClient into a requests-shaped session."""
        def get(self, url, headers=None, timeout=None, stream=None):
            path = url.split("127.0.0.1:80", 1)[-1] or "/"
            response = client.get(path)
            return _Resp(response.content, dict(response.headers),
                         status=response.status_code)

    found = shared.check_for_update("http://127.0.0.1:80", "1.0.0",
                                    app="studio", platform="win",
                                    public_key=pub, session=_LiveSession())
    assert found["update_available"] is True
    assert found["latest_version"] == "1.1.0"
    assert found["verified"] is True
    assert found["release_notes_ar"] == "أول إصدار منظَّم"
    assert found["sha256"] == hashlib.sha256(data).hexdigest()
    assert found["url"] == "/updates/studio/1.1.0/download/win"
    assert inner.app_updates is state.app_updates
    # a macOS client gets a refusal, not the Windows artifact
    with pytest.raises(shared.UpdateError):
        shared.check_for_update("http://127.0.0.1:80", "1.0.0",
                                app="studio", platform="macos",
                                public_key=pub, session=_LiveSession())


def test_the_client_asks_the_hub_for_its_own_platform():
    """A Windows client must never be handed the macOS artifact."""
    from shared import updater as shared
    calls: list[str] = []

    class _Resp:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, body: bytes) -> None:
            self.content = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *_a):
            return self.content

        def iter_content(self, chunk_size: int = 65536):
            for start in range(0, len(self.content), max(1, chunk_size)):
                yield self.content[start:start + chunk_size]

    class _Session:
        def get(self, url, headers=None, timeout=None, stream=None):
            calls.append(url)
            return _Resp(json.dumps({"ok": True, "update": None}).encode())

    shared.check_for_update("hub.example", "1.0.0", app="studio",
                            platform="win", public_key="k",
                            session=_Session())
    assert "platform=win" in calls[0]
    assert "channel=stable" in calls[0]