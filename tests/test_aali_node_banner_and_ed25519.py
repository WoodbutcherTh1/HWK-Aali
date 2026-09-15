"""Tests for the staged-update banner contract (Q5) + Ed25519 key loading.

Banner: ShellState must surface the daemon's structured ``update_staged``
field (version only) and the page must render it; the daemon must publish
it only on a successful stage.

Key loading: an Ed25519 seed hex in AALI_UPDATE_SIGNING_KEY must produce a
real Ed25519 key (sign→verify roundtrip through the same
sign_manifest/verify_manifest pair the wire uses) — this pins the
production path that was previously documented but unreachable from env.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from aali_hub import update_server as US
from aali_node import daemon as ND
from aali_node import shell as SH


# ---------------------------------------------------------------------------
# Q5: update_staged flows daemon → status → ShellState view
# ---------------------------------------------------------------------------
def test_fold_surfaces_update_staged_version() -> None:
    st = SH.ShellState()
    view = st.fold({"state": "connected",
                    "update_staged": "0.2.0"})
    assert view["update_staged"] == "0.2.0"


def test_fold_update_staged_defaults_empty() -> None:
    st = SH.ShellState()
    assert st.fold({"state": "starting"})["update_staged"] == ""


def test_fold_update_staged_is_json_serializable() -> None:
    st = SH.ShellState()
    view = st.fold({"state": "connected", "update_staged": "1.2.3"})
    assert json.loads(json.dumps(view))["update_staged"] == "1.2.3"


def test_page_renders_the_banner_for_staged_updates() -> None:
    """The banner element + its Arabic message must exist in the page."""
    assert 'id="ubanner"' in SH._PAGE
    assert "hidden" in SH._PAGE.split('id="ubanner"')[1].split(">")[0]
    assert "تحديث مُوقَّع جاهز للتفعيل" in SH._PAGE
    assert "dismissUpdate" in SH._PAGE


def test_run_node_starts_with_no_staged_update(tmp_path: Path) -> None:
    status: dict = {}
    ND.run_node("ws://127.0.0.1:1", "tok", tmp_path / "ws",
                status=status, stop=None and __import__("threading").Event())
    assert status["update_staged"] == ""


def test_daemon_publishes_version_on_successful_stage(
        tmp_path: Path, monkeypatch) -> None:
    """check_and_stage success → update_staged carries the VERSION only."""
    import threading

    import aali_node as pkg
    from aali_node import updater

    class FakeClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def check_and_stage(self, token: str):
            return Path("/staging-root/staged/9.9.9")

    monkeypatch.setattr(updater.UpdateClient, "__init__",
                        lambda self, *a, **kw: None)
    monkeypatch.setattr(updater.UpdateClient, "check_and_stage",
                        lambda self, token: Path("/x/staged/9.9.9"))
    monkeypatch.setattr(pkg, "__version__", "0.1.0", raising=False)

    status: dict = {}
    ND._run_update_check("http://hub", "tok", "key", tmp_path,
                         status=status)
    assert status["update_staged"] == "9.9.9"
    assert "verified update staged" in status["last_event"]


def test_failed_check_leaves_update_staged_empty(tmp_path: Path,
                                                 monkeypatch) -> None:
    from aali_node import updater

    def boom(*a, **kw):
        raise updater.NodeUpdateError("bad signature")

    monkeypatch.setattr(updater.UpdateClient, "check_and_stage", boom)
    status: dict = {}
    ND._run_update_check("http://hub", "tok", "key", tmp_path,
                         status=status)
    assert status.get("update_staged", "") == ""
    assert status["last_event"] == "update check failed (refused)"


# ---------------------------------------------------------------------------
# load_signing_key — the production Ed25519 path from env
# ---------------------------------------------------------------------------
def test_load_signing_key_hmac_fallback_for_arbitrary_strings() -> None:
    key = US.load_signing_key("my-hmac-secret")
    assert key == "my-hmac-secret"
    manifest = {"version": "1.0.0"}
    signed = US.sign_manifest(manifest, key)
    assert signed["sig_alg"] == "hmac-sha256"
    assert US.verify_manifest(signed, "my-hmac-secret")


def test_load_signing_key_refuses_empty() -> None:
    import pytest

    from aali_hub.update_server import UpdateError
    for empty in ("", "   ", b""):
        try:
            US.load_signing_key(empty)
        except UpdateError:
            continue
        pytest.fail(f"empty key {empty!r} must be refused")


def test_seed_hex_roundtrip_sign_verify() -> None:
    if not US.HAS_ED25519:
        import pytest
        pytest.skip("cryptography not installed in this venv")
    seed, pub = US.generate_signing_keypair()
    assert len(seed) == 64 and len(pub) == 64
    signer = US.load_signing_key(seed)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    assert isinstance(signer, Ed25519PrivateKey)

    manifest = US.build_manifest("1.2.3", "1.0.0", "/updates/1.2.3/download",
                                 "a" * 64)
    signed = US.sign_manifest(manifest, signer)
    assert signed["sig_alg"] == "ed25519"

    verifier = US.load_signing_key(pub, expect_public=True)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey)
    assert isinstance(verifier, Ed25519PublicKey)
    assert US.verify_manifest(signed, verifier) is True
    # tamper → refused
    tampered = dict(signed, version="9.9.9")
    assert US.verify_manifest(tampered, verifier) is False
    # a Node provisioned with only the SEED hex can verify too (the public
    # key is derived) — no second config value needed
    assert US.verify_manifest(signed, US.load_signing_key(seed)) is True


def test_64_hex_ambiguous_material_requires_intent() -> None:
    """Seed hex and public hex have the same shape — the intent must be
    stated. Default = private (sign); expect_public=True = verify."""
    import pytest

    from aali_hub.update_server import UpdateError

    hexkey = "ab" * 32
    signed = US.sign_manifest({"v": 1}, US.load_signing_key(hexkey))
    assert signed["sig_alg"] in ("ed25519", "hmac-sha256")
    # the SAME hex as a public key is a DIFFERENT key → signature refused
    verifier = US.load_signing_key(hexkey, expect_public=True)
    assert US.verify_manifest(signed, verifier) is False
    # non-hex with expect_public=True is refused, never guessed
    with pytest.raises(UpdateError, match="refusing to guess"):
        US.load_signing_key("my-hmac-secret", expect_public=True)


def test_hub_state_wires_signing_key_through_loader(monkeypatch) -> None:
    """create_app's UpdateStore must receive a LOADED key (seed hex →
    Ed25519), not the raw env string."""
    if importlib.util.find_spec("fastapi") is None:
        import pytest
        pytest.skip("fastapi not installed in this venv")
    if not US.HAS_ED25519:
        import pytest
        pytest.skip("cryptography not installed in this venv")

    from aali_hub.config import HubConfig
    from aali_hub.main import HubState

    seed, _pub = US.generate_signing_keypair()
    cfg = HubConfig(update_signing_key=seed)
    state = HubState(cfg)
    assert state.updates is not None
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    assert isinstance(state.updates.signing_key, Ed25519PrivateKey)
