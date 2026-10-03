"""Aali Hub — per-app, per-platform signed update store (studio · desktop · cli).

The single-app store in :mod:`aali_hub.update_server` exists for the Aali
Node and keeps working untouched (backward compatibility is a requirement, not
a courtesy). This module adds what three MORE clients need: one version with
a DIFFERENT artifact per platform.

On-disk layout (``<root>/{app}/{version}/{platform}/``)::

    artifact.zip        the bytes the owner uploaded
    artifact.sha256     its SHA-256, hex
    manifest.json       the SIGNED manifest (canonical, signature embedded)
    manifest.sig        the same signature, detached, for auditors/tools

Why both a signature inside the JSON and a ``.sig`` file: the embedded one is
what every client verifies (one code path — ``shared.updater.verify_manifest``),
and the detached file lets an operator check a release with
``openssl pkeyutl`` without parsing the JSON.

Security rules, in order of how often they save you:

1. **an unsigned artifact never enters the store.** ``publish`` verifies the
   detached Ed25519 signature over the artifact's sha256 BEFORE writing a
   single byte, using the store's own private key to derive the public half.
2. a declared sha256 that does not match the bytes is a refusal, not a warning.
3. ``retire`` refuses to remove the LAST version of an app — an empty feed
   would make every live client report "no updates" instead of a pinned older
   release.
4. the store re-verifies the manifest signature and the artifact hash on EVERY
   read. A file edited on disk after publication cannot be served.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aali_hub.update_server import (
    HAS_ED25519, sign_manifest, verify_manifest)

__all__ = ["AppUpdateStore", "AppUpdateError", "APPS", "PLATFORMS"]

APPS = ("studio", "desktop", "cli")
PLATFORMS = ("win", "macos", "linux")
CHANNELS = ("stable", "beta")

_VERSION_RE = re.compile(r"^\d+(\.\d+)+$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class AppUpdateError(Exception):
    """Expected store error: refused, missing, tampered, malformed."""


def _canonical(obj: dict[str, Any]) -> bytes:
    """Byte-identical to update_server._canonical — the clients verify this."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _safe(value: str, kind: str) -> str:
    """Reject anything that could escape the store root."""
    text = str(value or "").strip()
    if not text or not _SAFE_NAME.match(text) or text in (".", ".."):
        raise AppUpdateError(f"bad {kind}: {value!r}")
    return text


class AppUpdateStore:
    """Signed artifacts + manifests, per app and per platform."""

    def __init__(self, root: str | Path, signing_key: Any, *,
                 keep_versions: int = 3) -> None:
        self.root = Path(root)
        self.signing_key = signing_key
        self.keep_versions = max(1, int(keep_versions))
        self.root.mkdir(parents=True, exist_ok=True)

    # ————— paths —————
    def _version_dir(self, app: str, version: str) -> Path:
        return self.root / _safe(app, "app") / _safe(version, "version")

    def _platform_dir(self, app: str, version: str, platform: str) -> Path:
        return self._version_dir(app, version) / _safe(platform, "platform")

    def _manifest_path(self, app: str, version: str, platform: str) -> Path:
        return self._platform_dir(app, version, platform) / "manifest.json"

    def _sig_path(self, app: str, version: str, platform: str) -> Path:
        return self._platform_dir(app, version, platform) / "manifest.sig"

    def _sha_path(self, app: str, version: str, platform: str) -> Path:
        return self._platform_dir(app, version, platform) / "artifact.sha256"

    def _artifact_path(self, app: str, version: str, platform: str,
                       filename: str = "") -> Path:
        name = "artifact.zip"
        if filename:
            candidate = Path(str(filename)).name
            if _SAFE_NAME.match(candidate) and candidate.count(".") == 1:
                name = candidate
        return self._platform_dir(app, version, platform) / name

    def find_artifact(self, app: str, version: str, platform: str) -> Path:
        """The stored artifact, whatever extension it was published with.

        ``artifact.sha256`` shares the prefix and must NOT be matched — serving
        the hash file as the payload would download 65 bytes and fail the
        client's zip-slip/extract step with a baffling error.
        """
        folder = self._platform_dir(app, version, platform)
        if not folder.is_dir():
            raise AppUpdateError(
                f"no artifact for {app}/{version}/{platform}")
        for child in sorted(folder.iterdir()):
            if not child.is_file() or not child.name.startswith("artifact."):
                continue
            if child.suffix.lower() in (".sha256", ".sig", ".tmp"):
                continue
            return child
        raise AppUpdateError(f"no artifact for {app}/{version}/{platform}")

    # ————— publish —————
    def publish(self, app: str, version: str, platform: str, artifact: bytes,
                *, sha256: str = "", signature: str = "",
                channel: str = "stable", min_version: str = "",
                notes_ar: str = "", notes_en: str = "",
                filename: str = "") -> dict[str, Any]:
        """Verify, sign and store one (app, version, platform) artifact.

        Order matters: validation -> hash -> SIGNATURE -> write. A refusal
        never leaves a half-published version behind.
        """
        app = _safe(app, "app")
        if app not in APPS:
            raise AppUpdateError(f"unknown app: {app!r} (expected {APPS})")
        platform = _safe(platform, "platform")
        if platform not in PLATFORMS:
            raise AppUpdateError(
                f"unknown platform: {platform!r} (expected {PLATFORMS})")
        version = _safe(version, "version")
        if not _VERSION_RE.match(version):
            raise AppUpdateError(f"unparsable version: {version!r}")
        channel = str(channel or "stable").strip().lower()
        if channel not in CHANNELS:
            raise AppUpdateError(f"unknown channel: {channel!r}")
        if not artifact:
            raise AppUpdateError("empty artifact body")
        if min_version and not _VERSION_RE.match(str(min_version)):
            raise AppUpdateError(f"unparsable min_version: {min_version!r}")

        digest = hashlib.sha256(artifact).hexdigest()
        if sha256:
            declared = str(sha256).strip().lower()
            if declared != digest:
                raise AppUpdateError(
                    "declared sha256 does not match the uploaded bytes")
        if not signature:
            raise AppUpdateError(
                "refusing to publish an UNSIGNED artifact — sign the sha256 "
                "and pass ?sig=")
        # The detached signature covers the artifact's sha256 hex string, which
        # is exactly what shared.updater.download_and_verify re-checks on the
        # client. Verified HERE with the store's own key before writing.
        if not verify_detached_artifact(signature, digest, self.signing_key):
            raise AppUpdateError("artifact signature invalid — refusing")

        released = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rel_path = f"/updates/{app}/{version}/download/{platform}"
        manifest = {
            "app": app,
            "version": version,
            "platform": platform,
            "channel": channel,
            "min_version": str(min_version or ""),
            "url": rel_path,
            "sha256": digest,
            "artifact_signature": str(signature),
            "release_notes_ar": str(notes_ar or "")[:2000],
            "release_notes_en": str(notes_en or "")[:2000],
            "released": released,
        }
        signed = sign_manifest(manifest, self.signing_key)
        # Belt and braces: what we are about to store must verify with the key
        # we will verify it with.
        if not verify_manifest(signed, self.signing_key):
            raise AppUpdateError("manifest failed self-verification")

        folder = self._platform_dir(app, version, platform)
        folder.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._artifact_path(app, version, platform, filename),
                      artifact)
        _atomic_write(self._sha_path(app, version, platform),
                      (digest + "\n").encode("ascii"))
        _atomic_write(self._manifest_path(app, version, platform),
                      json.dumps(signed, ensure_ascii=True, indent=2
                                 ).encode("utf-8"))
        _atomic_write(self._sig_path(app, version, platform),
                      (str(signed["signature"]) + "\n").encode("ascii"))
        self._prune(app)
        return signed

    def _prune(self, app: str) -> None:
        app_dir = self.root / app
        if not app_dir.is_dir():
            return
        versions = self.list_versions(app)
        for old in versions[self.keep_versions:]:
            shutil.rmtree(app_dir / old, ignore_errors=True)

    # ————— read —————
    def manifest_for(self, app: str, version: str,
                     platform: str) -> dict[str, Any]:
        """One signed manifest, verified with the store key before it leaves."""
        path = self._manifest_path(app, version, platform)
        if not path.is_file():
            raise AppUpdateError(
                f"no manifest for {app}/{version}/{platform}")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AppUpdateError(
                f"unreadable manifest for {app}/{version}/{platform}") from exc
        if not isinstance(manifest, dict) or \
                not verify_manifest(manifest, self.signing_key):
            raise AppUpdateError(
                f"manifest signature invalid for {app}/{version}/{platform}")
        return manifest

    def version_manifest(self, app: str, version: str) -> dict[str, Any]:
        """The combined, SIGNED manifest for one version across platforms."""
        app = _safe(app, "app")
        version = _safe(version, "version")
        folder = self._version_dir(app, version)
        if not folder.is_dir():
            raise AppUpdateError(f"no release {version!r} for app {app!r}")
        platforms: dict[str, Any] = {}
        notes_ar = notes_en = ""
        channel = "stable"
        min_version = ""
        released = ""
        for child in sorted(folder.iterdir()):
            if not child.is_dir() or not (child / "manifest.json").is_file():
                continue
            manifest = self.manifest_for(app, version, child.name)
            platforms[child.name] = {
                "platform": manifest.get("platform"),
                "url": manifest.get("url"),
                "sha256": manifest.get("sha256"),
                "artifact_signature": manifest.get("artifact_signature"),
                "signature": manifest.get("signature"),
                "sig_alg": manifest.get("sig_alg"),
            }
            notes_ar = notes_ar or str(manifest.get("release_notes_ar") or "")
            notes_en = notes_en or str(manifest.get("release_notes_en") or "")
            channel = str(manifest.get("channel") or channel)
            min_version = min_version or str(manifest.get("min_version") or "")
            released = released or str(manifest.get("released") or "")
        if not platforms:
            raise AppUpdateError(f"release {version!r} has no platforms")
        combined = {
            "app": app,
            "version": version,
            "channel": channel,
            "min_version": min_version,
            "release_notes_ar": notes_ar,
            "release_notes_en": notes_en,
            "released": released,
            "platforms": platforms,
        }
        return sign_manifest(combined, self.signing_key)

    def latest(self, app: str, platform: str = "",
               channel: str = "") -> dict[str, Any]:
        """The newest release for *app*, as the client's signed manifest.

        ``platform`` picks WHICH manifest to return (a Windows client must
        never be handed the macOS artifact). ``channel='stable'`` restricts to
        stable releases; ``beta`` accepts any.
        """
        app = _safe(app, "app")
        if app not in APPS:
            raise AppUpdateError(f"unknown app: {app!r} (expected {APPS})")
        if platform and platform not in PLATFORMS:
            raise AppUpdateError(f"unknown platform: {platform!r}")
        want_channel = str(channel or "").strip().lower()
        if want_channel and want_channel not in CHANNELS:
            raise AppUpdateError(f"unknown channel: {channel!r}")
        for version in self.list_versions(app):
            for candidate in self._platforms_of(app, version):
                if platform and candidate != platform:
                    continue
                try:
                    manifest = self.manifest_for(app, version, candidate)
                except AppUpdateError:
                    continue
                if want_channel == "stable" and \
                        str(manifest.get("channel") or "stable") != "stable":
                    continue
                return manifest
        raise AppUpdateError(f"nothing published for app {app!r}")

    def _platforms_of(self, app: str, version: str) -> list[str]:
        folder = self._version_dir(app, version)
        if not folder.is_dir():
            return []
        return [child.name for child in sorted(folder.iterdir())
                if child.is_dir() and (child / "manifest.json").is_file()]

    def artifact_bytes(self, app: str, version: str, platform: str) -> bytes:
        """Artifact bytes, hash-checked against the signed manifest."""
        manifest = self.manifest_for(app, version, platform)
        path = self.find_artifact(app, version, platform)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AppUpdateError(f"artifact unreadable: {exc}") from exc
        if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
            raise AppUpdateError(
                f"artifact hash mismatch for {app}/{version}/{platform}")
        return data

    # ————— admin listing / retire —————
    def list_versions(self, app: str) -> list[str]:
        """Published versions, newest first (by publish mtime)."""
        app = _safe(app, "app")
        app_dir = self.root / app
        if not app_dir.is_dir():
            return []
        entries: list[tuple[float, str]] = []
        for child in app_dir.iterdir():
            if not child.is_dir():
                continue
            if not any((p / "manifest.json").is_file()
                       for p in child.iterdir() if p.is_dir()):
                continue
            try:
                entries.append((child.stat().st_mtime, child.name))
            except OSError:
                continue
        return [name for _mtime, name in sorted(entries, reverse=True)]

    def list_apps(self) -> dict[str, Any]:
        """What is published, per app — what /health reports."""
        out: dict[str, Any] = {}
        for app in APPS:
            versions = self.list_versions(app)
            platforms: list[str] = []
            for version in versions[:1]:
                platforms = self._platforms_of(app, version)
            out[app] = {
                "versions": versions,
                "latest": versions[0] if versions else "",
                "platforms": platforms,
            }
        return out

    def retire(self, app: str, version: str) -> None:
        """Remove one release (the rollback window).

        Refuses the LAST version: an empty feed would make every live client
        report "no updates" rather than staying on a pinned older release.
        """
        app = _safe(app, "app")
        version = _safe(version, "version")
        versions = self.list_versions(app)
        if version in versions and len(versions) <= 1:
            raise AppUpdateError(
                f"refusing to retire {version!r} for {app!r}: "
                "last remaining version")
        folder = self._version_dir(app, version)
        if not folder.is_dir():
            raise AppUpdateError(f"nothing published for {app}/{version}")
        # Verify at least one manifest before deleting anything, so a typo
        # cannot remove a release whose files are merely misnamed.
        self.manifest_for(app, version, self._platforms_of(app, version)[0])
        shutil.rmtree(folder, ignore_errors=True)


def verify_detached_artifact(signature: str, sha256: str,
                             key: Any) -> bool:
    """Verify the artifact's detached signature over its sha256 hex string.

    Ed25519 (production) or HMAC-SHA256 (dev-grade) — the same fallback the
    manifest signer uses, and the same message every client re-verifies.
    """
    if not signature or not sha256 or key is None:
        return False
    if HAS_ED25519:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey)
            if isinstance(key, Ed25519PrivateKey):
                key = key.public_key()
        except ImportError:  # pragma: no cover - cryptography is pinned
            pass
    return _verify_detached(signature, sha256, key)


def _verify_detached(signature: str, message: str, key: Any) -> bool:
    import hmac as hmac_mod
    try:
        sig = bytes.fromhex(str(signature).strip())
    except ValueError:
        return False
    payload = str(message).encode("utf-8")
    if HAS_ED25519:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        if isinstance(key, Ed25519PublicKey):
            try:
                key.verify(sig, payload)
                return True
            except Exception:  # noqa: BLE001 - InvalidSignature and friends
                return False
        if not isinstance(key, (str, bytes)):
            return False
    secret = key if isinstance(key, (str, bytes)) else None
    if secret is None:
        return False
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac_mod.compare_digest(sig.hex(),
                                   hmac_mod.new(secret, payload,
                                                hashlib.sha256).hexdigest())