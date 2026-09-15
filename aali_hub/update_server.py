"""Aali Hub update server — signed manifests + binaries for the Aali Node.

Manifest shape (stored as <version>.json next to the artifact):

    {version, min_version, url, sha256, signature,
     release_notes_ar, release_notes_en, created_at}

Signing: Ed25519 when the ``cryptography`` package is importable (production);
otherwise HMAC-SHA256 with ``AALI_UPDATE_SIGNING_KEY`` (dev-grade). The
signature algorithm is recorded in the manifest (``sig_alg``) so verifiers
dispatch correctly. Artifacts are written atomically (tmp + replace) and the
store keeps the newest ``keep_versions`` manifests for rollback.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey)
    _HAS_ED25519 = True
except ImportError:
    _HAS_ED25519 = False

__all__ = ["UpdateStore", "UpdateError", "sign_manifest", "verify_manifest",
           "HAS_ED25519", "build_manifest"]

HAS_ED25519 = _HAS_ED25519


class UpdateError(Exception):
    """Expected update-server error."""


def _canonical(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


# ---------------------------------------------------------------------------
# signing / verification
# ---------------------------------------------------------------------------
def sign_manifest(manifest: dict[str, Any], key: Any) -> dict[str, Any]:
    """Return a signed copy of *manifest* (adds signature + sig_alg)."""
    signed = dict(manifest)
    signed.pop("signature", None)
    if _HAS_ED25519 and isinstance(key, Ed25519PrivateKey):
        signed["sig_alg"] = "ed25519"
        signed["signature"] = key.sign(_canonical(signed)).hex()
    else:
        secret = key if isinstance(key, (str, bytes)) else str(key)
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        signed["sig_alg"] = "hmac-sha256"
        signed["signature"] = hmac_mod.new(secret, _canonical(signed),
                                           hashlib.sha256).hexdigest()
    return signed


def verify_manifest(manifest: dict[str, Any], key: Any) -> bool:
    """Verify a manifest signature (constant-time comparisons)."""
    signature = manifest.get("signature")
    if not signature:
        return False
    unsigned = {k: v for k, v in manifest.items()
                if k not in ("signature",)}
    alg = manifest.get("sig_alg", "hmac-sha256")
    if alg == "ed25519" and _HAS_ED25519 and isinstance(key, Ed25519PublicKey):
        try:
            key.verify(bytes.fromhex(signature), _canonical(unsigned))
            return True
        except Exception:
            return False
    secret = key if isinstance(key, (str, bytes)) else str(key)
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    expected = hmac_mod.new(secret, _canonical(unsigned),
                            hashlib.sha256).hexdigest()
    return hmac_mod.compare_digest(str(signature), expected)


def build_manifest(version: str, min_version: str, url: str, sha256: str, *,
                   release_notes_ar: str = "",
                   release_notes_en: str = "") -> dict[str, Any]:
    """Build an unsigned manifest dict with server timestamp."""
    return {
        "version": version,
        "min_version": min_version,
        "url": url,
        "sha256": sha256,
        "release_notes_ar": release_notes_ar,
        "release_notes_en": release_notes_en,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
class UpdateStore:
    """File-backed update artifact + manifest store with retention."""

    def __init__(self, root: str | Path, signing_key: Any, *,
                 keep_versions: int = 3) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.signing_key = signing_key
        self.keep_versions = max(1, keep_versions)

    def _artifact_path(self, version: str) -> Path:
        safe = Path(version).name
        if safe != version or not version:
            raise UpdateError(f"bad version string: {version!r}")
        return self.root / f"aali-node-{version}.zip"

    def _manifest_path(self, version: str) -> Path:
        return self.root / f"{version}.json"

    def publish(self, version: str, min_version: str, artifact: bytes, *,
                release_notes_ar: str = "",
                release_notes_en: str = "") -> dict[str, Any]:
        """Publish one release atomically; returns the signed manifest."""
        if not version or not isinstance(version, str):
            raise UpdateError("version must be a non-empty string")
        sha = hashlib.sha256(artifact).hexdigest()
        art_path = self._artifact_path(version)
        tmp = art_path.with_suffix(".tmp")
        tmp.write_bytes(artifact)
        os.replace(tmp, art_path)  # atomic on POSIX + Windows
        manifest = build_manifest(
            version, min_version,
            # the CANONICAL public endpoint (update_api serves
            # /updates/{version}/download) — a filename URL here 404s every
            # real Node (found live by scripts/smoke_aali_node.py; the unit
            # suite pinned the broken string instead of fetching it)
            url=f"/updates/{version}/download", sha256=sha,
            release_notes_ar=release_notes_ar,
            release_notes_en=release_notes_en)
        signed = sign_manifest(manifest, self.signing_key)
        mpath = self._manifest_path(version)
        mtmp = mpath.with_suffix(".json.tmp")
        mtmp.write_text(json.dumps(signed, ensure_ascii=True,
                                   indent=2), encoding="utf-8")
        os.replace(mtmp, mpath)
        self._prune()
        return signed

    def _prune(self) -> None:
        manifests = sorted(self.root.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        for old in manifests[self.keep_versions:]:
            version = old.stem
            self._artifact_path(version).unlink(missing_ok=True)
            old.unlink(missing_ok=True)

    def manifest_for(self, version: str) -> dict[str, Any]:
        """Load + verify one manifest; UpdateError when missing/tampered."""
        mpath = self._manifest_path(version)
        if not mpath.exists():
            raise UpdateError(f"no update published for {version!r}")
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        if not verify_manifest(manifest, self.signing_key):
            raise UpdateError(f"manifest signature invalid for {version!r}")
        return manifest

    def latest_manifest(self) -> dict[str, Any]:
        """The newest published, signature-valid manifest."""
        manifests = sorted(self.root.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        for mpath in manifests:
            try:
                return self.manifest_for(mpath.stem)
            except UpdateError:
                continue
        raise UpdateError("no updates published")

    def artifact_bytes(self, version: str) -> bytes:
        """Read one artifact after verifying its manifest signature + sha256."""
        manifest = self.manifest_for(version)
        art = self._artifact_path(version)
        if not art.exists():
            raise UpdateError(f"artifact missing for {version!r}")
        data = art.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise UpdateError(f"artifact hash mismatch for {version!r}")
        return data

    def list_versions(self) -> list[str]:
        """Published versions, newest first."""
        manifests = sorted(self.root.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.stem for p in manifests]

    def retire(self, version: str) -> None:
        """Remove one version's manifest + artifact (admin rollback window).

        UpdateError when the version was never published. Refuses to retire
        the LAST remaining version — retiring everything would make
        ``latest_manifest`` 404 and every live Node would report 'no
        updates' instead of a pinned older release.
        """
        if self._manifest_path(version).exists() and \
                len(self.list_versions()) <= 1:
            raise UpdateError(
                f"refusing to retire {version!r}: last remaining version")
        try:
            manifest = self.manifest_for(version)  # verifies signature
        except UpdateError as exc:
            raise UpdateError(f"cannot retire {version!r}: {exc}") from exc
        self._artifact_path(version).unlink(missing_ok=True)
        self._manifest_path(version).unlink(missing_ok=True)
