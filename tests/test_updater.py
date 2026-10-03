"""tests/test_updater.py — the universal signed-update contract (part 1).

Every test here is real: a real throwaway Ed25519 key signs real manifests
with the REAL hub signer (``aali_hub.update_server``), real zip/tar archives
are built and extracted, and the rollback test really swaps directories. Only
HTTP is faked, through a requests-shaped injected session.

The tripwires matter more than the happy paths: a batch/macOS/iOS build that
regresses into 'trust the hub', 'http to the internet', 'run the download' or
'extract outside the target' must fail HERE, loudly, not on the owner's Mac.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from shared import updater as U  # noqa: E402

PUB_HEX = ""
PRIV = None


def _keypair() -> None:
    global PUB_HEX, PRIV
    if PRIV is not None:
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    PRIV = Ed25519PrivateKey.generate()
    PUB_HEX = PRIV.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never touch the owner's real D:/hwk-data from a unit test."""
    _keypair()
    monkeypatch.setenv("AALI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AALI_UPDATE_INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.delenv("AALI_UPDATE_NO_RESTART", raising=False)
    U.configure(app="", install_dir=None, public_key=None,
                hub_url=U.DEFAULT_HUB_URL)


# ————— fakes —————
class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.content = body
        self.status_code = status
        self.headers = {"Content-Length": str(len(body))}

    def iter_content(self, chunk_size: int = 65536):
        for start in range(0, len(self.content), max(1, chunk_size)):
            yield self.content[start:start + chunk_size]

    def read(self, *_a, **_k):
        return self.content

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeSession:
    """requests-shaped; routes on a substring of the URL."""

    def __init__(self, routes: dict[str, tuple[bytes, int]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None, stream=None):
        self.calls.append(url)
        for needle, (body, status) in self.routes.items():
            if needle in url:
                return FakeResponse(body, status)
        return FakeResponse(b"{}", 404)


def sign_hex(message) -> str:
    return PRIV.sign(
        message if isinstance(message, bytes) else message.encode("utf-8")
    ).hex()


def signed_manifest(artifact: bytes, *, version="1.1.0", app="studio",
                    platform="win", min_version="1.0.0",
                    url="/updates/studio/1.1.0/download/win",
                    tamper=False, artifact_signature=True) -> dict:
    """Sign with the HUB's own signer so the client contract is pinned."""
    from aali_hub.update_server import build_manifest, sign_manifest
    digest = hashlib.sha256(artifact).hexdigest()
    manifest = build_manifest(version, min_version, url, digest,
                              release_notes_ar="إصدار تجريبي",
                              release_notes_en="test release")
    manifest["app"] = app
    manifest["platform"] = platform
    if artifact_signature:
        manifest["artifact_signature"] = sign_hex(digest)
    signed = sign_manifest(manifest, PRIV)
    if tamper:
        signed["sha256"] = "0" * 64
    return signed


def make_zip(entries: dict[str, str], *, name: str = "payload.zip") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    data = buf.getvalue()
    return data


def json_body(manifest) -> bytes:
    return json.dumps({"ok": True, "update": manifest}).encode("utf-8")


# ————— 1. check_for_update —————
def test_check_for_update_returns_the_documented_dict():
    artifact = make_zip({"Aali-Studio.exe": "binary"})
    manifest = signed_manifest(artifact)
    session = FakeSession({"/updates/studio/latest": (json_body(manifest), 200)})
    result = U.check_for_update("aali.dpdns.org", "1.0.0", app="studio",
                                platform="win", public_key=U.pubkey_from_hex(PUB_HEX),
                                session=session)
    for key in ("update_available", "auto_apply_allowed", "current_version",
                "latest_version", "min_version", "url", "absolute_url",
                "sha256", "signature", "artifact_signature",
                "release_notes_ar", "release_notes_en", "app", "platform",
                "channel", "manifest", "verified", "reason"):
        assert key in result, key
    assert result["update_available"] is True
    assert result["verified"] is True
    assert result["latest_version"] == "1.1.0"
    assert result["current_version"] == "1.0.0"
    assert result["auto_apply_allowed"] is True
    assert result["absolute_url"].startswith("https://aali.dpdns.org/updates/")
    # a bare host becomes https (never http)
    assert session.calls and session.calls[0].startswith("https://")


def test_check_for_update_up_to_date_is_not_an_error():
    manifest = signed_manifest(make_zip({"a": "b"}), version="1.0.0")
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    result = U.check_for_update("https://hub.example", "1.0.0",
                                public_key=U.pubkey_from_hex(PUB_HEX),
                                session=session)
    assert result["update_available"] is False
    assert result["reason"] == "up-to-date"


def test_check_for_update_with_nothing_published():
    session = FakeSession({"/latest": (json.dumps({"ok": True, "update": None})
                                        .encode(), 200)})
    result = U.check_for_update("https://hub.example", "1.0.0",
                                public_key=U.pubkey_from_hex(PUB_HEX),
                                session=session)
    assert result["update_available"] is False
    assert result["reason"] == "no-update"


def test_check_for_update_refuses_a_tampered_manifest():
    manifest = signed_manifest(make_zip({"a": "b"}), tamper=True)
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    with pytest.raises(U.UpdateError, match="signature invalid"):
        U.check_for_update("https://hub.example", "1.0.0",
                           public_key=U.pubkey_from_hex(PUB_HEX),
                           session=session)


def test_check_for_update_refuses_without_a_configured_key():
    """No public key = no trust. Never a silent 'update available'."""
    manifest = signed_manifest(make_zip({"a": "b"}))
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    with pytest.raises(U.UpdateError, match="signature invalid"):
        U.check_for_update("https://hub.example", "1.0.0", public_key=None,
                           session=session)


def test_check_for_update_refuses_another_platforms_artifact():
    manifest = signed_manifest(make_zip({"a": "b"}), platform="macos")
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    result = U.check_for_update("https://hub.example", "1.0.0", platform="win",
                                public_key=U.pubkey_from_hex(PUB_HEX),
                                session=session)
    assert result["update_available"] is False
    assert result["reason"] == "artifact-mismatch"


def test_check_for_update_refuses_an_absolute_manifest_url():
    manifest = signed_manifest(
        make_zip({"a": "b"}), url="https://evil.example/payload.zip")
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    with pytest.raises(U.UpdateError, match="non-relative"):
        U.check_for_update("https://hub.example", "1.0.0",
                           public_key=U.pubkey_from_hex(PUB_HEX),
                           session=session)


def test_check_for_update_flags_a_minimum_version_it_cannot_jump():
    manifest = signed_manifest(make_zip({"a": "b"}), min_version="2.0.0")
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    result = U.check_for_update("https://hub.example", "1.0.0",
                                public_key=U.pubkey_from_hex(PUB_HEX),
                                session=session)
    assert result["update_available"] is True
    assert result["auto_apply_allowed"] is False
    assert result["reason"] == "below-min-version"


def test_check_for_update_refuses_plain_http_off_localhost():
    manifest = signed_manifest(make_zip({"a": "b"}))
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    with pytest.raises(U.UpdateError, match="plain http"):
        U.check_for_update("http://hub.example", "1.0.0",
                           public_key=U.pubkey_from_hex(PUB_HEX),
                           session=session)
    # loopback dev is the one exemption
    assert U.check_for_update("http://127.0.0.1:8099", "1.0.0",
                              public_key=U.pubkey_from_hex(PUB_HEX),
                              session=session)["verified"] is True


def test_check_for_update_rejects_unknown_app_and_platform():
    for kwargs in ({"app": "nope"}, {"platform": "solaris"}):
        with pytest.raises(U.UpdateError):
            U.check_for_update("https://hub.example", "1.0.0",
                               public_key=U.pubkey_from_hex(PUB_HEX),
                               session=FakeSession({}), **kwargs)


def test_check_for_update_sends_the_bearer_token_header():
    seen: dict[str, object] = {}

    class HeaderSpy(FakeSession):
        def get(self, url, headers=None, timeout=None, stream=None):
            seen.update(headers or {})
            return super().get(url, headers=headers, timeout=timeout,
                               stream=stream)

    manifest = signed_manifest(make_zip({"a": "b"}))
    session = HeaderSpy({"/latest": (json_body(manifest), 200)})
    U.check_for_update("https://hub.example", "1.0.0", token="jwt-token",
                       public_key=U.pubkey_from_hex(PUB_HEX), session=session)
    assert seen.get("Authorization") == "Bearer jwt-token"


def test_unparsable_version_in_a_signed_manifest_fails_loudly():
    manifest = signed_manifest(make_zip({"a": "b"}), version="not-a-version")
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    with pytest.raises(U.UpdateError, match="unparsable version"):
        U.check_for_update("https://hub.example", "1.0.0",
                           public_key=U.pubkey_from_hex(PUB_HEX),
                           session=session)


# ————— 2. download_and_verify —————
def test_download_and_verify_happy_path(tmp_path):
    artifact = make_zip({"app/VERSION": "1.1.0"})
    digest = hashlib.sha256(artifact).hexdigest()
    session = FakeSession({"/download": (artifact, 200)})
    path = U.download_and_verify("https://hub.example/updates/x/download/win",
                                 digest, sign_hex(digest),
                                 U.pubkey_from_hex(PUB_HEX),
                                 dest_dir=tmp_path / "dl", session=session)
    assert path.is_file()
    assert path.read_bytes() == artifact
    assert path.name.endswith(f"{digest[:12]}.bin")


def test_download_and_verify_rejects_a_bad_sha256(tmp_path):
    artifact = make_zip({"a": "b"})
    good = hashlib.sha256(artifact).hexdigest()
    session = FakeSession({"/download": (artifact, 200)})
    with pytest.raises(U.UpdateError, match="sha256 mismatch"):
        U.download_and_verify("https://hub.example/updates/studio/1.1.0/download/win", "0" * 64,
                              sign_hex("0" * 64),
                              U.pubkey_from_hex(PUB_HEX),
                              dest_dir=tmp_path / "dl", session=session)
    assert not list((tmp_path / "dl").glob("*.bin"))


def test_download_and_verify_rejects_a_bad_signature(tmp_path):
    artifact = make_zip({"a": "b"})
    digest = hashlib.sha256(artifact).hexdigest()
    session = FakeSession({"/download": (artifact, 200)})
    with pytest.raises(U.UpdateError, match="signature invalid"):
        # correctly signed — but for a DIFFERENT artifact
        U.download_and_verify("https://hub.example/updates/studio/1.1.0/download/win",
                              digest, sign_hex("f" * 64),
                              U.pubkey_from_hex(PUB_HEX),
                              dest_dir=tmp_path / "dl", session=session)


def test_download_and_verify_rejects_a_missing_signature_when_a_key_is_set(tmp_path):
    artifact = make_zip({"a": "b"})
    digest = hashlib.sha256(artifact).hexdigest()
    session = FakeSession({"/download": (artifact, 200)})
    with pytest.raises(U.UpdateError, match="no artifact signature"):
        U.download_and_verify("https://hub.example/updates/studio/1.1.0/download/win", digest, "",
                              U.pubkey_from_hex(PUB_HEX),
                              dest_dir=tmp_path / "dl", session=session)


def test_download_and_verify_rejects_a_malformed_sha_argument(tmp_path):
    with pytest.raises(U.UpdateError, match="64-hex sha256"):
        U.download_and_verify("https://hub.example/updates/studio/1.1.0/download/win", "deadbeef", "",
                              U.pubkey_from_hex(PUB_HEX),
                              dest_dir=tmp_path / "dl",
                              session=FakeSession({}))


def test_download_and_verify_enforces_the_size_ceiling(tmp_path):
    artifact = make_zip({"a": "x" * 5000})
    digest = hashlib.sha256(artifact).hexdigest()
    session = FakeSession({"/download": (artifact, 200)})
    with pytest.raises(U.UpdateError, match="ceiling"):
        U.download_and_verify("https://hub.example/updates/studio/1.1.0/download/win",
                              digest, sign_hex(digest),
                              U.pubkey_from_hex(PUB_HEX),
                              dest_dir=tmp_path / "dl", session=session,
                              max_bytes=100)


def test_download_and_verify_refuses_http(tmp_path):
    artifact = make_zip({"a": "b"})
    digest = hashlib.sha256(artifact).hexdigest()
    session = FakeSession({"/d": (artifact, 200)})
    with pytest.raises(U.UpdateError, match="plain http"):
        U.download_and_verify("http://evil.example/updates/x/download/win", digest,
                              sign_hex(digest),
                              U.pubkey_from_hex(PUB_HEX),
                              dest_dir=tmp_path / "dl", session=session)


# ————— 3. zip_slip_guard —————
@pytest.mark.parametrize("member", [
    "../escape.txt",
    "sub/../../escape.txt",
    "/etc/passwd",
    "C:/Windows/win.ini",
    "\\\\server\\share\\x.txt",
    "%2e%2e/escape.txt",
    "sub\\..\\..\\escape.txt",
    "NUL",
    "aux.txt",
])
def test_zip_slip_guard_rejects_malicious_archives(tmp_path, member):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, "pwned")
    archive = tmp_path / "evil.zip"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(U.UpdateError, match="zip-slip refused"):
        U.zip_slip_guard(archive, tmp_path / "target")
    assert not (tmp_path / "escape.txt").exists()


def test_zip_slip_guard_rejects_a_zip_symlink_member(tmp_path):
    """A clean name is still a slip primitive when it is a symlink."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("innocent.txt")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    archive = tmp_path / "link.zip"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(U.UpdateError, match="symlink"):
        U.zip_slip_guard(archive, tmp_path / "target")


def test_zip_slip_guard_rejects_a_tar_symlink_member(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("innocent.txt")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    archive = tmp_path / "link.tar"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(U.UpdateError, match="link/device"):
        U.zip_slip_guard(archive, tmp_path / "target")


def test_zip_slip_guard_accepts_a_clean_archive(tmp_path):
    archive = tmp_path / "good.zip"
    archive.write_bytes(make_zip({"Aali-Studio/VERSION": "1.1.0",
                                  "Aali-Studio/app/main.py": "print(1)\n"}))
    assert U.zip_slip_guard(archive, tmp_path / "target") is None


def test_zip_slip_guard_refuses_an_unreadable_archive(tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not an archive at all")
    with pytest.raises(U.UpdateError):
        U.zip_slip_guard(junk, tmp_path / "target")


# ————— 4. stage_update —————
def test_stage_update_extracts_correctly(tmp_path):
    install = tmp_path / "install"
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"app/main.py": "print(1)\n",
                                  "app/data.txt": "hello"}))
    staged = U.stage_update(archive, install)
    assert staged == install / "staged" / "1.1.0"
    assert (staged / "app" / "main.py").read_text() == "print(1)\n"
    assert (staged / "VERSION").read_text().strip() == "1.1.0"


def test_stage_update_leaves_the_running_install_alone(tmp_path):
    install = tmp_path / "install"
    current = install / "current"
    current.mkdir(parents=True)
    (current / "VERSION").write_text("1.0.0\n")
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"app/new.py": "x\n"}))
    U.stage_update(archive, install)
    assert (current / "VERSION").read_text().strip() == "1.0.0"
    assert not (current / "app").exists()


def test_stage_update_refuses_a_malicious_archive(tmp_path):
    install = tmp_path / "install"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.txt", "pwned")
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(U.UpdateError, match="zip-slip refused"):
        U.stage_update(archive, install)
    assert not (tmp_path.parent / "evil.txt").exists()


def test_stage_update_refuses_an_unparsable_version(tmp_path):
    archive = tmp_path / "payload.zip"
    archive.write_bytes(make_zip({"a": "b"}))
    with pytest.raises(U.UpdateError, match="unparsable version"):
        U.stage_update(archive, tmp_path / "install", version="latest")


def test_stage_update_supports_tar(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"print(2)\n"
        info = tarfile.TarInfo("app/run.py")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    archive = tmp_path / "Aali-Studio-v1.2.0-macos.tar.gz"
    archive.write_bytes(buf.getvalue())
    staged = U.stage_update(archive, tmp_path / "install")
    assert (staged / "app" / "run.py").read_bytes() == b"print(2)\n"


# ————— 5. apply_and_restart + rollback —————
def _installed(tmp_path, version="1.0.0"):
    install = tmp_path / "install"
    current = install / "current"
    current.mkdir(parents=True)
    (current / "VERSION").write_text(version + "\n")
    (current / "marker.txt").write_text(f"payload {version}")
    return install


def test_apply_and_restart_swaps_and_retains_the_previous(tmp_path):
    install = _installed(tmp_path)
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"marker.txt": "payload 1.1.0"}))
    staged = U.stage_update(archive, install)
    spawned: list[int] = []
    U.apply_and_restart(staged, install, spawn=lambda: spawned.append(1))
    assert (install / "current" / "VERSION").read_text().strip() == "1.1.0"
    previous = install / "previous" / "1.0.0"
    assert previous.is_dir()
    assert (previous / "marker.txt").read_text() == "payload 1.0.0"
    assert spawned == [1], "the restart must be scheduled"
    state = U.read_state(install)
    assert state["version"] == "1.1.0"
    assert state["previous"] == "1.0.0"
    assert state["pending_ack"] is True
    assert state["failures"] == 0


def test_apply_and_restart_refuses_an_unstaged_payload(tmp_path):
    install = _installed(tmp_path)
    rogue = tmp_path / "somewhere" / "1.5.0"
    rogue.mkdir(parents=True)
    (rogue / "VERSION").write_text("1.5.0\n")
    with pytest.raises(U.UpdateError, match="not staged"):
        U.apply_and_restart(rogue, install, spawn=lambda: None)


def test_apply_and_restart_refuses_a_payload_without_a_version_marker(tmp_path):
    install = _installed(tmp_path)
    staged = install / "staged" / "9.9.9"
    staged.mkdir(parents=True)
    with pytest.raises(U.UpdateError, match="VERSION marker"):
        U.apply_and_restart(staged, install, spawn=lambda: None)


def test_rollback_restores_the_previous_version_after_two_failed_launches(tmp_path):
    install = _installed(tmp_path)
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"marker.txt": "payload 1.1.0"}))
    staged = U.stage_update(archive, install)
    U.apply_and_restart(staged, install, spawn=lambda: None)

    first = U.register_startup(install)
    assert first["status"] == "failed-launch"
    assert (install / "current" / "VERSION").read_text().strip() == "1.1.0"

    second = U.register_startup(install)
    assert second["status"] == "rolled-back"
    assert (install / "current" / "VERSION").read_text().strip() == "1.0.0"
    assert (install / "current" / "marker.txt").read_text() == "payload 1.0.0"
    state = U.read_state(install)
    assert state["rolled_back_from"] == "1.1.0"
    assert state["version"] == "1.0.0"


def test_a_healthy_launch_records_ok_and_never_rolls_back(tmp_path):
    install = _installed(tmp_path)
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"marker.txt": "payload 1.1.0"}))
    staged = U.stage_update(archive, install)
    U.apply_and_restart(staged, install, spawn=lambda: None)
    U.register_startup(install)          # first boot of the new payload
    U.record_launch_ok(install)          # it reached a working UI
    assert U.register_startup(install)["status"] == "clean"
    assert (install / "current" / "VERSION").read_text().strip() == "1.1.0"


def test_rollback_if_failed_skips_below_the_threshold(tmp_path):
    install = _installed(tmp_path)
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"marker.txt": "payload 1.1.0"}))
    staged = U.stage_update(archive, install)
    U.apply_and_restart(staged, install, spawn=lambda: None)
    U.register_startup(install)
    U.rollback_if_failed(install)          # only ONE failure so far
    assert (install / "current" / "VERSION").read_text().strip() == "1.1.0"


def test_rollback_if_failed_honours_force(tmp_path):
    install = _installed(tmp_path)
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"marker.txt": "payload 1.1.0"}))
    staged = U.stage_update(archive, install)
    U.apply_and_restart(staged, install, spawn=lambda: None)
    U.rollback_if_failed(install, force=True)   # the UI's 'go back' button
    assert (install / "current" / "VERSION").read_text().strip() == "1.0.0"


def test_rollback_with_no_previous_payload_is_an_honest_no_op(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    (install / "current").mkdir()
    (install / "current" / "VERSION").write_text("1.1.0\n")
    U._write_state(install, {"version": "1.1.0", "failures": 5,
                             "pending_ack": True, "previous": "",
                             "previous_dir": ""})
    U.rollback_if_failed(install)
    assert (install / "current" / "VERSION").read_text().strip() == "1.1.0"


def test_rollback_if_failed_works_with_no_arguments(tmp_path):
    """The spec's zero-arg call: the client configured its install dir."""
    install = _installed(tmp_path)
    U.configure(app="studio", install_dir=install)
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"marker.txt": "payload 1.1.0"}))
    staged = U.stage_update(archive, install)
    U.apply_and_restart(staged, install, spawn=lambda: None)
    U.register_startup()
    U.register_startup()
    assert (install / "current" / "VERSION").read_text().strip() == "1.0.0"


# ————— 6. no-auto-execute (tripwires) —————
def test_updater_source_never_executes_anything_it_downloaded():
    source = (REPO / "scripts" / "shared" / "updater.py").read_text("utf-8")
    for forbidden in ("shell=True", "os.system(", "os.popen", "eval(",
                      "exec(", "__import__"):
        assert forbidden not in source, forbidden


def test_apply_and_restart_never_spawns_when_restart_is_off(tmp_path):
    install = _installed(tmp_path)
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"marker.txt": "payload 1.1.0"}))
    staged = U.stage_update(archive, install)
    spawned: list[int] = []
    U.apply_and_restart(staged, install, restart=False,
                        spawn=lambda: spawned.append(1))
    assert spawned == []
    assert (install / "current" / "VERSION").read_text().strip() == "1.1.0"


def test_default_spawn_re_executes_only_the_running_executable():
    source = (REPO / "scripts" / "shared" / "updater.py").read_text("utf-8")
    start = source.index("def _default_spawn")
    end = source.index("def apply_and_restart")
    body = source[start:end]
    assert "sys.executable" in body
    for forbidden in ("staged", "current", "update-", "payload"):
        assert forbidden not in body, forbidden


def test_staged_payload_is_data_only_never_marked_executable(tmp_path):
    """No chmod +x anywhere: the payload is swapped in as plain files."""
    source = (REPO / "scripts" / "shared" / "updater.py").read_text("utf-8")
    assert "chmod" not in source
    install = _installed(tmp_path)
    archive = tmp_path / "Aali-Studio-v1.1.0-win.zip"
    archive.write_bytes(make_zip({"app.sh": "#!/bin/sh\necho hi\n"}))
    staged = U.stage_update(archive, install)
    mode = (staged / "app.sh").stat().st_mode
    assert not mode & 0o111, oct(mode)


# ————— 7. contract parity + log hygiene —————
def test_client_manifest_verification_matches_the_real_hub_verifier():
    from aali_hub.update_server import verify_manifest as hub_verify
    artifact = make_zip({"a": "b"})
    manifest = signed_manifest(artifact)
    key = U.pubkey_from_hex(PUB_HEX)
    assert U.verify_manifest(manifest, key) is True
    assert hub_verify(manifest, PRIV) is True
    broken = dict(manifest, version="9.9.9")
    assert U.verify_manifest(broken, key) is False
    assert hub_verify(broken, PRIV) is False


def test_the_hub_side_routes_this_client_expects_exist():
    """Part 5's contract: /updates/{app}/latest is served by the hub router.

    Not a route-hit test (FastAPI lives in .venv-hub, not the training venv)
    — a source pin so the client path can never drift from the hub's prefix.
    """
    from aali_hub import update_api
    source = (REPO / "aali_hub" / "update_api.py").read_text("utf-8")
    assert 'prefix="/updates"' in source
    assert update_api.__all__  # module imports without fastapi installed


def test_pubkey_from_hex_refuses_anything_but_a_public_key():
    with pytest.raises(U.UpdateError, match="64-hex"):
        U.pubkey_from_hex("deadbeef")
    with pytest.raises(U.UpdateError):
        U.pubkey_from_hex(None)
    with pytest.raises(U.UpdateError):
        U.pubkey_from_hex(12345)


def test_an_unknown_signature_algorithm_is_refused_not_guessed():
    manifest = signed_manifest(make_zip({"a": "b"}))
    manifest["sig_alg"] = "rot13"
    assert U.verify_manifest(manifest, U.pubkey_from_hex(PUB_HEX)) is False


def test_versions_never_win_by_being_unparsable():
    assert U.is_newer("999.garbage", "1.0.0") is False
    assert U.is_newer("1.1", "1.0.9") is True
    assert U.is_newer("1.0", "1.0.0") is False
    assert U.parse_version("v2.0.1") == (2, 0, 1)
    assert U.parse_version("") is None
    assert U.parse_version(None) is None


def test_the_update_log_is_content_free(tmp_path):
    U.log_event("test_event", app="studio", version="1.1.0",
                sha256_16="a" * 64, reason="ok",
                url="https://hub.example/secret/path?token=abc",
                token="super-secret", content="user text here")
    path = U.update_log_path()
    assert path.is_file()
    raw = path.read_text(encoding="utf-8")
    assert "super-secret" not in raw
    assert "user text here" not in raw
    assert "secret/path" not in raw
    record = U.read_log_tail(1)[0]
    assert record["sha256_16"] == "a" * 16
    assert record["event"] == "test_event"
    assert "url" not in record and "token" not in record


def test_the_log_survives_an_unwritable_data_root(monkeypatch, tmp_path):
    blocker = tmp_path / "i-am-a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("AALI_DATA_DIR", str(blocker))
    U.log_event("unwritable")   # must not raise at the call site
    assert blocker.is_file()    # and must not have touched the blocker


def test_check_writes_a_content_free_record_of_its_outcome(tmp_path):
    artifact = make_zip({"a": "b"})
    manifest = signed_manifest(artifact)
    session = FakeSession({"/latest": (json_body(manifest), 200)})
    U.check_for_update("https://hub.example", "1.0.0",
                       public_key=U.pubkey_from_hex(PUB_HEX), session=session)
    events = [r.get("event") for r in U.read_log_tail(20)]
    assert "check_ok" in events
    assert "check_failed" not in events


def test_a_failed_check_logs_and_raises_rather_than_lying(tmp_path):
    session = FakeSession({"/latest": (b"<html>gateway error</html>", 200)})
    with pytest.raises(U.UpdateError, match="malformed manifest"):
        U.check_for_update("https://hub.example", "1.0.0",
                           public_key=U.pubkey_from_hex(PUB_HEX), session=session)
    assert "check_failed" in [r.get("event") for r in U.read_log_tail(20)]


def test_http_error_status_is_reported_not_swallowed():
    session = FakeSession({"/latest": (b"nope", 503)})
    with pytest.raises(U.UpdateError, match="HTTP 503"):
        U.check_for_update("https://hub.example", "1.0.0",
                           public_key=U.pubkey_from_hex(PUB_HEX), session=session)


def test_the_urllib_fallback_works_too(monkeypatch, tmp_path):
    """The .venv-desktop PyInstaller builds have NO requests — this must work."""
    artifact = make_zip({"a": "b"})
    digest = hashlib.sha256(artifact).hexdigest()
    body = json_body(signed_manifest(artifact))

    def opener(request, timeout=None):
        path = request.full_url
        payload = body if "/latest" in path else artifact
        return FakeResponse(payload)

    result = U.check_for_update("http://127.0.0.1:9", "1.0.0",
                                public_key=U.pubkey_from_hex(PUB_HEX),
                                opener=opener)
    assert result["verified"] is True
    path = U.download_and_verify("http://127.0.0.1:9/updates/studio/1.1.0/download/win",
                                 digest, sign_hex(digest), U.pubkey_from_hex(PUB_HEX),
                                 dest_dir=tmp_path / "dl", opener=opener)
    assert path.read_bytes() == artifact


# ————— 8. the REAL transports, end to end, over a real loopback hub —————
class _HubHandler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"])
                  .BaseHTTPRequestHandler):
    """A miniature signed hub: real HTTP, real bytes, real Ed25519 manifest."""

    artifact = b""
    manifest_body = b"{}"

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.startswith("/updates/studio/1.1.0/download/win"):
            body, ctype = self.artifact, "application/zip"
        elif self.path.startswith("/updates/studio/latest"):
            body, ctype = self.manifest_body, "application/json"
        elif self.path.startswith("/updates/studio/1.1.0/manifest"):
            body = json.dumps({"ok": True, "update": self.manifest}).encode()
            ctype = "application/json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):  # silence the test server
        return


@pytest.fixture
def live_hub():
    """A real HTTP server on 127.0.0.1 serving a real signed manifest."""
    import threading
    from http.server import ThreadingHTTPServer

    artifact = make_zip({"Aali-Studio/VERSION": "1.1.0",
                         "Aali-Studio/app/main.py": "print('new')\n"})
    manifest = signed_manifest(
        artifact, url="/updates/studio/1.1.0/download/win")
    _HubHandler.artifact = artifact
    _HubHandler.manifest = manifest
    _HubHandler.manifest_body = json_body(manifest)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", manifest, artifact
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("transport", ["auto", "urllib"])
def test_full_update_round_trip_over_real_http(live_hub, tmp_path, transport):
    """check -> download -> verify -> stage -> apply -> two failed launches -> rollback.

    Real sockets, real archives, real directory swaps. Only the transport is
    optionally pinned to the stdlib path the .venv-desktop builds use.
    """
    hub_url, manifest, artifact = live_hub
    key = U.pubkey_from_hex(PUB_HEX)
    extra = {} if transport == "auto" else {"opener": urllib.request.urlopen}
    install = tmp_path / "install"
    current = install / "current"
    current.mkdir(parents=True)
    (current / "VERSION").write_text("1.0.0\n")

    found = U.check_for_update(hub_url, "1.0.0", app="studio", platform="win",
                               public_key=key, **extra)
    assert found["update_available"] is True
    assert found["verified"] is True
    assert found["reason"] == "update-available"

    downloaded = U.download_and_verify(
        found["absolute_url"], found["sha256"], found["artifact_signature"],
        key, dest_dir=tmp_path / "dl", **extra)
    assert downloaded.read_bytes() == artifact

    staged = U.stage_update(downloaded, install)
    assert (staged / "Aali-Studio" / "app" / "main.py").read_text() == \
        "print('new')\n"

    U.apply_and_restart(staged, install, spawn=lambda: None)
    assert (install / "current" / "Aali-Studio" / "app" / "main.py").is_file()
    assert (install / "previous" / "1.0.0" / "VERSION").is_file()

    U.register_startup(install)
    assert U.register_startup(install)["status"] == "rolled-back"
    assert (install / "current" / "VERSION").read_text().strip() == "1.0.0"

    events = [r.get("event") for r in U.read_log_tail(30)]
    for expected in ("check_ok", "download_ok", "staged", "applied",
                     "launch_unacked", "rolled_back"):
        assert expected in events, f"{expected} missing from {events}"


def test_a_tampered_artifact_is_rejected_over_real_http(live_hub, tmp_path):
    """The bytes on the wire are swapped AFTER signing — the client says no."""
    hub_url, manifest, _artifact = live_hub
    key = U.pubkey_from_hex(PUB_HEX)
    _HubHandler.artifact = make_zip({"Aali-Studio/VERSION": "9.9.9"})
    try:
        with pytest.raises(U.UpdateError, match="sha256 mismatch"):
            U.download_and_verify(f"{hub_url}/updates/studio/1.1.0/download/win",
                                  manifest["sha256"],
                                  manifest["artifact_signature"], key,
                                  dest_dir=tmp_path / "dl")
    finally:
        _HubHandler.artifact = _artifact
    assert not list((tmp_path / "dl").glob("*.bin"))


def test_configure_rejects_an_unknown_app_and_records_the_rest(tmp_path):
    U.configure(app="desktop", install_dir=tmp_path / "x",
                public_key="k", hub_url="https://hub.example")
    cfg = U.current_config()
    assert cfg["app"] == "desktop"
    assert cfg["hub_url"] == "https://hub.example"
    with pytest.raises(U.UpdateError, match="unknown app"):
        U.configure(app="brain")