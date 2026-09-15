"""Node-side signed update client — verification contract tests.

The updater must FAIL CLOSED: an unverifiable manifest, a hash mismatch, or
a malicious archive never reaches disk as a usable staged update. HTTP is
injected (no network in tests).
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
for p in (str(ROOT), str(ROOT / "file-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

from aali_hub import update_server as us  # noqa: E402
from aali_node import updater  # noqa: E402

KEY = "test-update-signing-key"
VERSION = "0.2.0"
CURRENT = "0.1.0"


def _make_artifact() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("aali_node/__init__.py", '__version__ = "0.2.0"\n')
        zf.writestr("aali_node/updater.py", "# newer module\n")
    return buf.getvalue()


@pytest.fixture()
def manifest() -> dict:
    return us.sign_manifest(
        us.build_manifest(VERSION, "0.0.0", "/updates/aali-node-0.2.0.zip",
                          hashlib.sha256(_make_artifact()).hexdigest()),
        KEY)


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _client(tmp_path: Path, responses: dict[str, bytes],
            statuses: dict[str, int] | None = None) -> updater.UpdateClient:
    """Client whose HTTP layer answers from a dict — no network."""
    statuses = statuses or {}

    def fake_opener(request, timeout: float = 0.0):  # noqa: ARG001
        path = request.full_url.split("?")[0].rstrip("/")
        key = ("/updates/latest" if path.endswith("/updates/latest")
               else path[path.rfind("/updates/"):])
        if key not in responses:
            raise AssertionError(f"unexpected URL: {request.full_url}")
        return _FakeResponse(responses[key], statuses.get(key, 200))

    return updater.UpdateClient(
        "http://hub.test", KEY, tmp_path / "install",
        current_version=CURRENT, timeout=0.1, opener=fake_opener)


# ---------------------------------------------------------------------------
# version parsing
# ---------------------------------------------------------------------------
def test_parse_version_strict() -> None:
    assert updater.parse_version("1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.2") == (1, 2)
    assert updater.parse_version("01.02.03") == (1, 2, 3)
    assert updater.parse_version("1.x") is None
    assert updater.parse_version("999.garbage") is None
    assert updater.parse_version("") is None
    assert updater.parse_version(None) is None


def test_is_newer_semantics() -> None:
    assert updater._is_newer("0.2.0", "0.1.9")
    assert updater._is_newer("1.0", "0.9.9")
    assert not updater._is_newer("1.2", "1.2.0")   # padded compare
    assert not updater._is_newer("1.2.0", "1.2")
    assert not updater._is_newer("999.garbage", "0.0.1")  # unparsable never wins


# ---------------------------------------------------------------------------
# check_for_updates — fail closed
# ---------------------------------------------------------------------------
def test_check_returns_newer_manifest(tmp_path: Path, manifest: dict) -> None:
    client = _client(tmp_path, {
        "/updates/latest": json.dumps({"ok": True, "update": manifest}).encode(),
    })
    out = client.check_for_updates("tok")
    assert out is not None
    assert out["version"] == VERSION


def test_check_returns_none_when_up_to_date(tmp_path: Path,
                                            manifest: dict) -> None:
    old = dict(manifest, version=CURRENT)
    # re-sign under the CURRENT version so the manifest itself is valid
    old = us.sign_manifest(
        us.build_manifest(CURRENT, "0.0.0", "/updates/aali-node-0.1.0.zip",
                          hashlib.sha256(_make_artifact()).hexdigest()), KEY)
    client = _client(tmp_path, {
        "/updates/latest": json.dumps({"ok": True, "update": old}).encode(),
    })
    assert client.check_for_updates("tok") is None


def test_check_rejects_forged_signature(tmp_path: Path,
                                        manifest: dict) -> None:
    forged = dict(manifest, version="9.9.9")  # re-signed by nobody
    client = _client(tmp_path, {
        "/updates/latest": json.dumps(
            {"ok": True, "update": forged}).encode(),
    })
    with pytest.raises(updater.NodeUpdateError, match="signature"):
        client.check_for_updates("tok")


def test_check_rejects_wrong_key(tmp_path: Path, manifest: dict) -> None:
    client = updater.UpdateClient(
        "http://hub.test", "a-different-key", tmp_path / "install",
        current_version=CURRENT,
        opener=_client(tmp_path, {
            "/updates/latest": json.dumps(
                {"ok": True, "update": manifest}).encode(),
        }).opener)
    with pytest.raises(updater.NodeUpdateError, match="signature"):
        client.check_for_updates("tok")


def test_check_rejects_unparsable_version(tmp_path: Path) -> None:
    evil = us.sign_manifest(
        us.build_manifest("999.garbage", "0.0.0", "/updates/x.zip",
                          "0" * 64), KEY)
    client = _client(tmp_path, {
        "/updates/latest": json.dumps({"ok": True, "update": evil}).encode(),
    })
    with pytest.raises(updater.NodeUpdateError, match="unparsable"):
        client.check_for_updates("tok")


def test_check_fails_closed_on_http_error(tmp_path: Path) -> None:
    client = _client(tmp_path, {
        "/updates/latest": b"server error",
        }, statuses={"/updates/latest": 404})
    with pytest.raises(updater.NodeUpdateError, match="HTTP 404"):
        client.check_for_updates("tok")


def test_check_rejects_malformed_body(tmp_path: Path) -> None:
    client = _client(tmp_path, {"/updates/latest": b"not-json{"})
    with pytest.raises(updater.NodeUpdateError):
        client.check_for_updates("tok")


# ---------------------------------------------------------------------------
# stage_update — verify before write, zip-slip refused
# ---------------------------------------------------------------------------
def test_stage_writes_verified_artifact(tmp_path: Path,
                                        manifest: dict) -> None:
    art = _make_artifact()
    client = _client(tmp_path, {
        "/updates/latest": json.dumps({"ok": True, "update": manifest}).encode(),
        "/updates/aali-node-0.2.0.zip": art,
    })
    staged = client.stage_update("tok", manifest)
    assert staged.is_dir()
    assert (staged / "aali_node" / "__init__.py").read_text(encoding="utf-8") \
        == '__version__ = "0.2.0"\n'
    # running install untouched
    assert not (tmp_path / "install" / "aali_node").exists()


def test_stage_refuses_hash_mismatch(tmp_path: Path, manifest: dict) -> None:
    client = _client(tmp_path, {
        "/updates/aali-node-0.2.0.zip": b"totally-different-bytes",
    })
    with pytest.raises(updater.NodeUpdateError, match="sha256"):
        client.stage_update("tok", manifest)
    staged = tmp_path / "install" / "staged"
    assert not staged.exists() or not any(staged.iterdir())


def test_stage_refuses_absolute_url(tmp_path: Path, manifest: dict) -> None:
    evil = dict(manifest, url="http://evil.example/payload.zip")
    # signature no longer valid after mutation → refused before the URL check
    client = _client(tmp_path, {})
    with pytest.raises(updater.NodeUpdateError):
        client.stage_update("tok", evil)


def test_stage_refuses_zip_slip(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    art = buf.getvalue()
    evil = us.sign_manifest(
        us.build_manifest(VERSION, "0.0.0", "/updates/aali-node-0.2.0.zip",
                          hashlib.sha256(art).hexdigest()), KEY)
    client = _client(tmp_path, {
        "/updates/aali-node-0.2.0.zip": art,
    })
    with pytest.raises(updater.NodeUpdateError, match="zip-slip"):
        client.stage_update("tok", evil)
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "install" / "staged" / VERSION).exists()


def test_stage_replaces_previous_staging(tmp_path: Path,
                                         manifest: dict) -> None:
    art = _make_artifact()
    client = _client(tmp_path, {
        "/updates/aali-node-0.2.0.zip": art,
    })
    first = client.stage_update("tok", manifest)
    second = client.stage_update("tok", manifest)
    assert first == second
    assert first.is_dir()


def test_check_and_stage_end_to_end(tmp_path: Path, manifest: dict) -> None:
    client = _client(tmp_path, {
        "/updates/latest": json.dumps({"ok": True, "update": manifest}).encode(),
        "/updates/aali-node-0.2.0.zip": _make_artifact(),
    })
    staged = client.check_and_stage("tok")
    assert staged is not None and staged.is_dir()


def test_check_and_stage_none_when_current(tmp_path: Path) -> None:
    old = us.sign_manifest(
        us.build_manifest(CURRENT, "0.0.0", "/updates/aali-node-0.1.0.zip",
                          hashlib.sha256(_make_artifact()).hexdigest()), KEY)
    client = _client(tmp_path, {
        "/updates/latest": json.dumps({"ok": True, "update": old}).encode(),
    })
    assert client.check_and_stage("tok") is None
