"""Aali Node updater — signed update checks against the Aali Hub.

The Node never trusts the Hub's word: every manifest is signature-verified
(aali_hub.update_server.verify_manifest — Ed25519 via `cryptography`, or
HMAC-SHA256 with a provisioned shared key) and every artifact is sha256-checked
against its manifest BEFORE anything is written or staged. Anything
unverifiable fails closed with NodeUpdateError.

stdlib-only HTTP (urllib) so the updater runs in every venv and stays
importable wherever the sandbox is (mirrors the daemon's lazy-import rule).

Semver comparison uses a conservative pair-of-int-tuples parse: a version
that does not parse as d[.d[.d...]] is NEVER considered newer (an attacker
supplying "999.garbage" gains nothing).

Staging never self-overwrites the running install: artifacts land in
``<install_root>/staged/<version>/`` with zip-slip refusal; ACTIVATION
(swap + restart) is a later, explicitly-owned step — this module only ever
prepares a verified candidate directory.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

__all__ = ["NodeUpdateError", "UpdateClient", "parse_version"]


class NodeUpdateError(Exception):
    """Expected updater failure (unverifiable, unavailable, malformed)."""


def parse_version(version: str) -> tuple[int, ...] | None:
    """Parse '1.2.3' → (1, 2, 3); None when not plain dotted digits."""
    if not isinstance(version, str) or not version:
        return None
    parts = version.split(".")
    if not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _is_newer(candidate: str, current: str) -> bool:
    """True only when candidate > current under the strict dotted parse."""
    cand = parse_version(candidate)
    cur = parse_version(current)
    if cand is None or cur is None:
        return False  # unparsable versions never win
    # pad to equal length so 1.2 > 1.2.0 is False but 1.3 > 1.2.9 is True
    width = max(len(cand), len(cur))
    cand += (0,) * (width - len(cand))
    cur += (0,) * (width - len(cur))
    return cand > cur


class UpdateClient:
    """Checks the Hub's update surface and stages verified artifacts."""

    def __init__(self, hub_base_url: str, verification_key: Any,
                 install_root: str | Path, *, current_version: str = "0.0.0",
                 timeout: float = 30.0, opener: Any = None) -> None:
        self.hub_base_url = hub_base_url.rstrip("/")
        self.verification_key = verification_key
        self.install_root = Path(install_root)
        self.current_version = current_version
        self.timeout = timeout
        self.opener = opener  # injectable urlopener for tests

    # -- HTTP ---------------------------------------------------------------
    def _get_json(self, path: str, token: str) -> dict[str, Any]:
        url = f"{self.hub_base_url}{path}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
        })
        try:
            opener = self.opener or urllib.request.urlopen
            with opener(req, timeout=self.timeout) as resp:  # type: ignore[operator]
                if getattr(resp, "status", 200) != 200:
                    raise NodeUpdateError(
                        f"update check failed: HTTP {resp.status}")
                return json.loads(resp.read().decode("utf-8"))
        except NodeUpdateError:
            raise
        except urllib.error.HTTPError as exc:
            raise NodeUpdateError(
                f"update check failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise NodeUpdateError(f"update check failed: {exc}") from exc

    def _get_bytes(self, url: str, token: str) -> bytes:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
        })
        try:
            opener = self.opener or urllib.request.urlopen
            with opener(req, timeout=self.timeout) as resp:  # type: ignore[operator]
                if getattr(resp, "status", 200) != 200:
                    raise NodeUpdateError(
                        f"update download failed: HTTP {resp.status}")
                return resp.read()
        except NodeUpdateError:
            raise
        except urllib.error.HTTPError as exc:
            raise NodeUpdateError(
                f"update download failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise NodeUpdateError(f"update download failed: {exc}") from exc

    # -- checks ---------------------------------------------------------------
    def check_for_updates(self, token: str) -> dict[str, Any] | None:
        """Return the newer manifest, or None when current is the latest.

        Fails closed: a Hub that answers garbage, an unverifiable signature,
        or an unparsable version is an error — never a silent 'stay put'.
        """
        try:
            body = self._get_json("/updates/latest", token)
        except NodeUpdateError:
            raise
        except Exception as exc:  # injected-opener AssertionErrors etc.
            raise NodeUpdateError(f"update check failed: {exc}") from exc
        manifest = body.get("update") if isinstance(body, dict) else None
        if not isinstance(manifest, dict):
            raise NodeUpdateError("malformed manifest response")
        from aali_hub.update_server import verify_manifest
        if not verify_manifest(manifest, self.verification_key):
            raise NodeUpdateError(
                "manifest signature invalid — refusing the update")
        version = str(manifest.get("version", ""))
        if parse_version(version) is None:
            # A SIGNED release we cannot compare must fail loudly: silent
            # 'stay put' would hide a version-scheme change forever.
            raise NodeUpdateError(
                f"unparsable version in signed manifest: {version!r}")
        if not _is_newer(version, self.current_version):
            return None
        return manifest

    def stage_update(self, token: str, manifest: dict[str, Any],
                     *, staging_root: str | Path | None = None) -> Path:
        """Download + verify + unzip the artifact into a staging directory.

        Returns the staged directory. Every step verifies before writing:
        sha256 first, then a zip-slip-checked extraction. The currently
        running install is never touched.
        """
        from aali_hub.update_server import verify_manifest

        if not isinstance(manifest, dict) or not verify_manifest(
                manifest, self.verification_key):
            raise NodeUpdateError(
                "manifest signature invalid — refusing the update")
        version = str(manifest.get("version", ""))
        parsed = parse_version(version)
        if parsed is None:
            raise NodeUpdateError(f"unparsable version: {version!r}")
        url = str(manifest.get("url", ""))
        if not url.startswith("/"):
            raise NodeUpdateError(
                f"refusing non-relative update url: {url!r}")

        data = self._get_bytes(f"{self.hub_base_url}{url}", token)
        if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
            raise NodeUpdateError(
                "artifact sha256 mismatch — refusing the update")

        root = Path(staging_root) if staging_root else \
            self.install_root / "staged"
        dest = root / version
        if dest.resolve() == self.install_root.resolve():
            raise NodeUpdateError("staging path collides with install root")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmpdir = Path(tempfile.mkdtemp(prefix="aali-update-",
                                       dir=str(dest.parent)))
        try:
            with zipfile.ZipFile(io_bytes(data)) as zf:
                for member in zf.namelist():
                    target = (tmpdir / member).resolve()
                    if not str(target).startswith(str(tmpdir.resolve())):
                        raise NodeUpdateError(
                            f"zip-slip refused: {member!r}")
                zf.extractall(tmpdir)
            if dest.exists():
                shutil.rmtree(dest)
            tmpdir.rename(dest)
        except NodeUpdateError:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise NodeUpdateError(f"staging failed: {exc}") from exc
        return dest

    def check_and_stage(self, token: str) -> Path | None:
        """One call for embedders: stage when newer, None when up to date."""
        manifest = self.check_for_updates(token)
        if manifest is None:
            return None
        return self.stage_update(token, manifest)


def io_bytes(data: bytes) -> Any:
    """Bytes → zipfile-compatible file object (stdlib io.BytesIO)."""
    import io
    return io.BytesIO(data)
