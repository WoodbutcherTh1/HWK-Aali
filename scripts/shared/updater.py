"""Aali universal updater — ONE signed-update contract for Studio, Desktop, CLI.

This is the shared module behind "publish once, users update themselves". It
mirrors the already-shipped Aali Node updater (``aali_node/updater.py``) so
there is ONE security story to audit, not three:

* **Never trust the hub.** The hub's manifest signature is re-verified
  CLIENT-SIDE (Ed25519 through ``cryptography``, HMAC-SHA256 for dev-grade),
  and the artifact is sha256-checked plus detached-signature-checked BEFORE a
  single byte is written into an install.
* **No auto-execute.** Nothing downloaded is ever run. The only process this
  module ever starts is a re-exec of the ALREADY-RUNNING executable.
* **Zip-slip / tar-slip refusal** on every member, plus refusal of symlinks,
  device names, drive letters, UNC and %-encoded traversal.
* **Rollback.** The previous payload is retained; two failed launches of a
  freshly applied version restore it automatically.
* **HTTPS only** for any non-loopback hub.
* **Content-free log** at ``<data root>/updates/update.log``.

Install layout owned by this module (one directory per client):

    <install_dir>/current/          the live payload
    <install_dir>/previous/<ver>/   the retained previous payload
    <install_dir>/staged/<ver>/     verified, not-yet-activated candidates
    <install_dir>/update_state.json {version, previous, failures, pending_ack}

HTTP: ``requests`` is used when importable (the owner asked for it and it
ships in the training venv) with a stdlib ``urllib`` fallback, because the
PyInstaller client builds (``.venv-desktop``) do NOT have requests installed —
a hard dependency there would ship a broken Studio .exe. ``cryptography`` is
the only hard requirement (it is in both venvs and is already bundled for the
Node's updater).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "UpdateError",
    "APPS",
    "PLATFORMS",
    "MAX_FAILURES",
    "configure",
    "current_config",
    "check_for_update",
    "download_and_verify",
    "stage_update",
    "apply_and_restart",
    "rollback_if_failed",
    "zip_slip_guard",
    "register_startup",
    "record_launch_ok",
    "restart_now",
    "parse_version",
    "is_newer",
    "host_platform",
    "pubkey_from_hex",
    "verify_detached",
    "verify_manifest",
    "normalize_hub_url",
    "check_secure_url",
    "update_log_path",
    "read_log_tail",
]

APPS = ("studio", "desktop", "cli")
PLATFORMS = ("win", "macos", "linux")

#: Two failed launches in a row roll back (the owner's rule).
MAX_FAILURES = 2

#: Hard ceiling on a downloaded artifact (a Studio zip is ~100-200 MB).
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024

DEFAULT_HUB_URL = "https://aali.dpdns.org"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}

STATE_FILE = "update_state.json"
CURRENT_DIR = "current"
PREVIOUS_DIR = "previous"
STAGED_DIR = "staged"

_WINDOWS_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class UpdateError(Exception):
    """Expected updater failure: unverifiable, malformed, refused, offline."""


# ---------------------------------------------------------------------------
# process-wide client configuration (so the spec's zero-arg rollback works)
# ---------------------------------------------------------------------------
_CONFIG: dict[str, Any] = {
    "app": "",
    "install_dir": None,
    "public_key": None,
    "hub_url": DEFAULT_HUB_URL,
}


def configure(*, app: str = "", install_dir: str | os.PathLike[str] | None = None,
              public_key: Any = None, hub_url: str = "") -> dict[str, Any]:
    """Tell the shared module which client it is running inside.

    Every client calls this once at import/startup. It is what lets
    ``rollback_if_failed()`` work with no arguments, as specified.
    """
    if app:
        if app not in APPS:
            raise UpdateError(f"unknown app: {app!r} (expected {APPS})")
        _CONFIG["app"] = app
    if install_dir is not None:
        _CONFIG["install_dir"] = Path(install_dir)
    if public_key is not None:
        _CONFIG["public_key"] = public_key
    if hub_url:
        _CONFIG["hub_url"] = hub_url
    return dict(_CONFIG)


def current_config() -> dict[str, Any]:
    """A copy of the active client configuration."""
    return dict(_CONFIG)


def _install_dir(install_dir: str | os.PathLike[str] | None) -> Path:
    raw = install_dir if install_dir is not None else _CONFIG.get("install_dir")
    if raw is None:
        raw = os.getenv("AALI_UPDATE_INSTALL_DIR") or None
    if not raw:
        raise UpdateError(
            "no install dir configured — call configure(install_dir=...) or "
            "set AALI_UPDATE_INSTALL_DIR")
    return Path(raw)


def _app_name() -> str:
    """The configured app id, or '' when no client registered itself."""
    return str(_CONFIG.get("app") or os.getenv("AALI_UPDATE_APP") or "")


# ---------------------------------------------------------------------------
# content-free log
# ---------------------------------------------------------------------------
def _data_root() -> Path:
    """The machine data root, via hwk_paths when it is importable.

    The fallback mirrors hwk_paths.data_root() so the module still works in a
    frozen build that somehow lost file-agent/ — an updater that cannot log is
    not a reason to skip verification.
    """
    try:
        from file_agent import hwk_paths  # type: ignore
        return hwk_paths.data_root()
    except Exception:  # noqa: BLE001 - any import problem falls through
        pass
    override = os.getenv("AALI_DATA_DIR")
    if override:
        return Path(override)
    candidate = Path("D:/hwk-data")
    if os.name == "nt" and candidate.exists():
        return candidate
    return Path(os.path.expanduser("~")) / "hwk-data"


def update_log_path() -> Path:
    """``D:/hwk-data/updates/update.log`` on the PC, ``~/hwk-data/...`` on a Mac."""
    return _data_root() / "updates" / "update.log"


#: Fields that may appear in a log line. Anything else is dropped, so no URL
#: path, query string, token, file content or user text can leak by accident.
_LOG_FIELDS = ("app", "platform", "channel", "version", "previous", "result",
               "reason", "size", "sha256_16", "failures", "http_status",
               "transport")


def log_event(event: str, **fields: Any) -> None:
    """Append one CONTENT-FREE JSONL record. Never raises at the call site."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event,
              "app": _app_name() or "client"}
    for key in _LOG_FIELDS:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key == "sha256_16" and isinstance(value, str):
                value = value[:16]
            if isinstance(value, (str, int, float, bool)):
                record[key] = value
    try:
        path = update_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        # A read-only disk, a full volume or an unwritable path must never
        # break an update: the log is diagnostics, not the security boundary.
        pass


def read_log_tail(limit: int = 40) -> list[dict[str, Any]]:
    """Last *limit* log records (used by the UI's diagnostics panel)."""
    path = update_log_path()
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, int(limit)):]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


# ---------------------------------------------------------------------------
# versions / platform
# ---------------------------------------------------------------------------
def parse_version(version: Any) -> tuple[int, ...] | None:
    """'1.2.3' -> (1, 2, 3); None when not plain dotted digits.

    Conservative on purpose: an unparsable version never wins a comparison, so
    an attacker offering '999.garbage' gains nothing.
    """
    if not isinstance(version, str) or not version:
        return None
    text = version.strip().lstrip("vV")
    if not text:
        return None
    parts = text.split(".")
    if not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def is_newer(candidate: Any, current: Any) -> bool:
    """True only when candidate > current under the strict dotted parse."""
    cand = parse_version(candidate)
    cur = parse_version(current)
    if cand is None or cur is None:
        return False
    width = max(len(cand), len(cur))
    cand += (0,) * (width - len(cand))
    cur += (0,) * (width - len(cur))
    return cand > cur


def host_platform() -> str:
    """'win' | 'macos' | 'linux' — the hub's per-platform artifact key."""
    if os.name == "nt":
        return "win"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# ---------------------------------------------------------------------------
# keys / signatures
# ---------------------------------------------------------------------------
def pubkey_from_hex(hex_text: Any) -> Any:
    """64-hex → Ed25519PublicKey. Refuses anything else.

    Mirrors aali_hub.update_server.load_signing_key(expect_public=True): seed
    hex and public hex are textually identical, so intent is stated, never
    guessed. Returns None when `cryptography` is unavailable.
    """
    if not isinstance(hex_text, str):
        raise UpdateError("public key must be a 64-hex string, not "
                          f"{type(hex_text).__name__}")
    text = hex_text.strip().lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise UpdateError(
            "expected a 64-hex Ed25519 public key — refusing to guess what "
            "this key material is")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
    except ImportError as exc:  # pragma: no cover - cryptography is pinned
        raise UpdateError(
            "Ed25519 needs the 'cryptography' package") from exc
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(text))


def verify_detached(signature: Any, message: Any, pubkey: Any) -> bool:
    """Verify a detached signature over *message*.

    * message: ``str``/``bytes`` — the exact bytes that were signed.
    * pubkey: an Ed25519PublicKey (production) or a str/bytes shared secret
      (dev-grade HMAC-SHA256, the same fallback the hub uses).
    """
    if isinstance(message, str):
        payload = message.encode("utf-8")
    elif isinstance(message, (bytes, bytearray)):
        payload = bytes(message)
    else:
        return False
    if not signature or pubkey is None:
        return False
    if isinstance(signature, str):
        try:
            sig = bytes.fromhex(signature.strip())
        except ValueError:
            return False
    elif isinstance(signature, (bytes, bytearray)):
        sig = bytes(signature)
    else:
        return False

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey, Ed25519PrivateKey)
    except ImportError:
        Ed25519PublicKey = Ed25519PrivateKey = ()  # type: ignore[assignment]
    if isinstance(pubkey, Ed25519PrivateKey):
        pubkey = pubkey.public_key()
    if Ed25519PublicKey and isinstance(pubkey, Ed25519PublicKey):
        try:
            pubkey.verify(sig, payload)
            return True
        except Exception:  # noqa: BLE001 - InvalidSignature and friends
            return False
    secret = pubkey if isinstance(pubkey, (str, bytes)) else None
    if secret is None:
        return False
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    expected = hmac_mod.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac_mod.compare_digest(sig.hex(), expected)


def _canonical(obj: dict[str, Any]) -> bytes:
    """Byte-identical to aali_hub.update_server._canonical (manifest signing)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def verify_manifest(manifest: Any, pubkey: Any) -> bool:
    """Client-side verification of a HUB manifest signature.

    Re-implemented here on purpose: a frozen Studio .exe must be able to verify
    a manifest without importing the whole aali_hub package. The canonical form
    and the algorithm names match the hub exactly, which
    ``tests/test_updater.py`` pins against the real
    ``aali_hub.update_server`` in the same suite.
    """
    if not isinstance(manifest, dict) or not pubkey:
        return False
    signature = manifest.get("signature")
    if not signature:
        return False
    unsigned = {k: v for k, v in manifest.items() if k != "signature"}
    alg = manifest.get("sig_alg", "hmac-sha256")
    if alg == "ed25519":
        return verify_detached(signature, _canonical(unsigned), pubkey)
    if alg != "hmac-sha256":
        return False  # an unknown algorithm is a refusal, never a guess
    if not isinstance(pubkey, (str, bytes)):
        return False
    return verify_detached(signature, _canonical(unsigned), pubkey)


# ---------------------------------------------------------------------------
# URL policy
# ---------------------------------------------------------------------------
def normalize_hub_url(hub_url: Any) -> str:
    """Bare host -> https://host; trailing slash removed; scheme enforced."""
    if not isinstance(hub_url, str) or not hub_url.strip():
        raise UpdateError("hub url is empty")
    text = hub_url.strip().rstrip("/")
    if "://" not in text:
        text = f"https://{text}"
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https"):
        raise UpdateError(f"unsupported hub scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise UpdateError(f"hub url has no host: {hub_url!r}")
    return text


def check_secure_url(url: Any) -> str:
    """Public HTTPS policy check (normalize + enforce), for UIs that save a hub URL.

    Saves the "you typed http:// and only found out three clicks later" class
    of surprise: the same rule the transport enforces, callable at save time.
    """
    return _require_secure(normalize_hub_url(url))


def _require_secure(url: str) -> str:
    """HTTPS for everything except loopback (dev). Hard rule, no env escape."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise UpdateError(f"unsupported url scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in LOOPBACK_HOSTS:
        raise UpdateError(
            f"refusing plain http for a non-localhost hub ({host or '?'}) — "
            "use https")
    return url


def _resolve_relative(base: str, url: Any) -> str:
    """A signed manifest may only point at a PATH on the hub we already trust.

    An absolute URL in a signed manifest would let a compromised hub aim the
    client at a third-party host; the Node updater refuses it for the same
    reason and so does this one.
    """
    if not isinstance(url, str) or not url.strip():
        raise UpdateError("manifest has no url")
    if "://" in url:
        raise UpdateError(f"refusing non-relative update url: {url!r}")
    if not url.startswith("/"):
        raise UpdateError(f"refusing non-relative update url: {url!r}")
    return f"{base.rstrip('/')}{url}"


# ---------------------------------------------------------------------------
# HTTP (requests when available, stdlib urllib otherwise)
# ---------------------------------------------------------------------------
class _UrllibResponse:
    """Minimal requests-shaped wrapper so one read path serves both."""

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status
        self.headers = headers
        self.content = body

    def iter_content(self, chunk_size: int = 65536):  # noqa: ANN201
        data = self.content
        for start in range(0, len(data), max(1, chunk_size)):
            yield data[start:start + chunk_size]


def _http_get(url: str, *, headers: dict[str, str] | None = None,
              timeout: float = 20.0, session: Any = None,
              opener: Any = None,
              max_bytes: int = MAX_DOWNLOAD_BYTES) -> tuple[bytes, int, str]:
    """GET *url* → (body, status, transport). Raises UpdateError on failure."""
    _require_secure(url)
    head = dict(headers or {})
    if session is not None:
        return _http_get_requests(url, head, timeout, session, max_bytes)
    try:
        import requests  # noqa: PLC0415 - optional by design
    except ImportError:
        requests = None  # type: ignore[assignment]
    if requests is not None and opener is None:
        return _http_get_requests(url, head, timeout, requests, max_bytes)
    return _http_get_urllib(url, head, timeout, opener, max_bytes)


def _http_get_requests(url: str, headers: dict[str, str], timeout: float,
                       requests_or_session: Any,
                       max_bytes: int) -> tuple[bytes, int, str]:
    getter = getattr(requests_or_session, "get", None)
    if getter is None:
        raise UpdateError("injected session has no .get()")
    try:
        response = getter(url, headers=headers, timeout=timeout, stream=True)
    except TypeError:
        # a fake session that only accepts (url, headers, timeout)
        response = getter(url, headers=headers, timeout=timeout)
    status = int(getattr(response, "status_code", 200) or 200)
    if status != 200:
        raise UpdateError(f"update download failed: HTTP {status}")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(65536):
        if not chunk:
            continue
        size += len(chunk)
        if size > max_bytes:
            raise UpdateError(
                f"artifact exceeds the {max_bytes} byte ceiling — refused")
        chunks.append(chunk)
    transport = "requests"
    module = requests_or_session.__class__.__module__ or ""
    if module.startswith("unittest.mock") or "fake" in module.lower():
        transport = "requests(fake)"
    return b"".join(chunks), status, transport


def _http_get_urllib(url: str, headers: dict[str, str], timeout: float,
                     opener: Any, max_bytes: int) -> tuple[bytes, int, str]:
    request = urllib.request.Request(url, headers=headers)
    fn = opener or urllib.request.urlopen
    try:
        with fn(request, timeout=timeout) as response:  # type: ignore[operator]
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise UpdateError(f"update download failed: HTTP {status}")
            declared = response.headers.get("Content-Length")  # type: ignore[union-attr]
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise UpdateError(
                    f"artifact exceeds the {max_bytes} byte ceiling — refused")
            body = response.read(max_bytes + 1)
    except UpdateError:
        raise
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"update download failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"update download failed: {exc}") from exc
    if len(body) > max_bytes:
        raise UpdateError(f"artifact exceeds the {max_bytes} byte ceiling — refused")
    return body, status, "urllib"


# ---------------------------------------------------------------------------
# [1] check_for_update
# ---------------------------------------------------------------------------
def check_for_update(hub_url: str, current_version: str, *,
                     app: str = "", platform: str = "", channel: str = "stable",
                     public_key: Any = None, token: str = "",
                     timeout: float = 20.0, session: Any = None,
                     opener: Any = None) -> dict[str, Any]:
    """Ask the hub whether a newer signed release exists for this client.

    Returns a plain dict (never None) so a UI can render it unconditionally::

        update_available   bool
        auto_apply_allowed bool   (False below min_version, or on mismatch)
        current_version / latest_version / min_version
        url / absolute_url           url is the signed RELATIVE path
        sha256 / signature / artifact_signature
        release_notes_ar / release_notes_en
        app / platform / channel / manifest / verified / reason

    Fails CLOSED with UpdateError when the manifest is unsigned, wrongly
    signed, for another platform/app, or the version is unparsable. 'No update
    published' is NOT an error — it returns update_available False with
    reason 'no-update'.
    """
    client_app = app or _app_name() or "studio"  # default client identity
    if client_app not in APPS:
        raise UpdateError(f"unknown app: {client_app!r} (expected {APPS})")
    plat = platform or host_platform()
    if plat not in PLATFORMS:
        raise UpdateError(f"unknown platform: {plat!r} (expected {PLATFORMS})")
    key = public_key if public_key is not None else _CONFIG.get("public_key")
    base = normalize_hub_url(hub_url or _CONFIG.get("hub_url")
                             or DEFAULT_HUB_URL)
    # The hub answers with the artifact for THIS platform only: a Windows
    # client must never be handed the macOS build (it would "update" into
    # something it cannot run). Older hubs ignore the parameter.
    params = [f"platform={urllib.parse.quote(plat)}"]
    if channel:
        params.append(f"channel={urllib.parse.quote(str(channel))}")
    path = f"/updates/{client_app}/latest?" + "&".join(params)
    url = f"{base}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    result: dict[str, Any] = {
        "update_available": False,
        "auto_apply_allowed": False,
        "app": client_app,
        "platform": plat,
        "channel": channel,
        "current_version": current_version,
        "latest_version": "",
        "min_version": "",
        "url": "",
        "absolute_url": "",
        "sha256": "",
        "signature": "",
        "artifact_signature": "",
        "release_notes_ar": "",
        "release_notes_en": "",
        "manifest": {},
        "verified": False,
        "reason": "",
    }
    try:
        body_bytes, status, transport = _http_get(
            url, headers=headers, timeout=timeout, session=session,
            opener=opener)
    except UpdateError as exc:
        log_event("check_failed", app=client_app, platform=plat,
                  channel=channel, reason=str(exc)[:120])
        raise
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        log_event("check_failed", app=client_app, platform=plat,
                  channel=channel, reason="malformed-json", http_status=status)
        raise UpdateError("malformed manifest response") from exc
    manifest = payload.get("update") if isinstance(payload, dict) else None
    if not isinstance(manifest, dict):
        log_event("check_failed", app=client_app, platform=plat,
                  channel=channel, reason="no-update", http_status=status)
        result["reason"] = "no-update"
        return result

    # 1. the hub's claim is verified HERE, not trusted.
    if not verify_manifest(manifest, key):
        log_event("check_refused", app=client_app, platform=plat,
                  channel=channel, version=str(manifest.get("version", ""))[:32],
                  reason="bad-manifest-signature")
        raise UpdateError(
            "manifest signature invalid — refusing the update (no public key "
            "configured?)")

    version = str(manifest.get("version", ""))
    if parse_version(version) is None:
        raise UpdateError(
            f"unparsable version in signed manifest: {version!r}")

    # 2. the artifact must be for THIS platform and THIS app.
    manifest_app = str(manifest.get("app", client_app))
    manifest_platform = str(manifest.get("platform", plat))
    if manifest_app != client_app or manifest_platform != plat:
        result.update({
            "latest_version": version,
            "manifest": manifest,
            "verified": True,
            "reason": "artifact-mismatch",
        })
        log_event("check_refused", app=client_app, platform=plat,
                  channel=channel, version=version, reason="artifact-mismatch")
        return result

    latest_path = _resolve_relative(base, manifest.get("url"))
    result.update({
        "latest_version": version,
        "min_version": str(manifest.get("min_version", "") or ""),
        "url": str(manifest.get("url")),
        "absolute_url": latest_path,
        "sha256": str(manifest.get("sha256", "")),
        "signature": str(manifest.get("signature", "")),
        "artifact_signature": str(manifest.get("artifact_signature", "")
                                  or manifest.get("signature_artifact", "")),
        "release_notes_ar": str(manifest.get("release_notes_ar", "") or ""),
        "release_notes_en": str(manifest.get("release_notes_en", "") or ""),
        "manifest": manifest,
        "verified": True,
    })
    if not is_newer(version, current_version):
        result["reason"] = "up-to-date"
        log_event("check_ok", app=client_app, platform=plat, channel=channel,
                  version=version, result="up-to-date", transport=transport)
        return result
    result["update_available"] = True
    minimum = parse_version(result["min_version"])
    current = parse_version(current_version)
    if minimum is not None and (current is None or current < minimum):
        # current < min_version -> a manual upgrade is required; never silent.
        result["auto_apply_allowed"] = False
        result["reason"] = "below-min-version"
    else:
        result["auto_apply_allowed"] = True
        result["reason"] = "update-available"
    log_event("check_ok", app=client_app, platform=plat, channel=channel,
              version=version, result=result["reason"],
              sha256_16=result["sha256"], transport=transport)
    return result


# ---------------------------------------------------------------------------
# [2] download_and_verify
# ---------------------------------------------------------------------------
def download_and_verify(url: str, sha256: str, sig: Any, pubkey: Any, *,
                        dest_dir: str | os.PathLike[str] | None = None,
                        token: str = "", timeout: float = 60.0,
                        session: Any = None, opener: Any = None,
                        max_bytes: int = MAX_DOWNLOAD_BYTES) -> Path:
    """Download an artifact and verify it BEFORE it is handed to the caller.

    Order matters: HTTPS policy, size ceiling, **sha256 of the bytes**, then
    the detached signature over that sha256 hex string. A mismatch raises and
    the temp file is removed — a partially-verified artifact is never returned.
    """
    if not isinstance(sha256, str) or len(sha256) != 64 or \
            any(c not in "0123456789abcdef" for c in sha256.lower()):
        raise UpdateError(f"expected a 64-hex sha256, got {sha256!r}")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body, status, transport = _http_get(
        url, headers=headers, timeout=timeout, session=session, opener=opener,
        max_bytes=max_bytes)
    digest = hashlib.sha256(body).hexdigest()
    if digest != sha256.lower():
        log_event("download_rejected", reason="sha256-mismatch",
                  sha256_16=sha256, http_status=status, transport=transport)
        raise UpdateError("artifact sha256 mismatch — refusing the update")
    if sig:
        if not verify_detached(sig, digest, pubkey):
            log_event("download_rejected", reason="bad-artifact-signature",
                      sha256_16=sha256, http_status=status, transport=transport)
            raise UpdateError(
                "artifact signature invalid — refusing the update")
    elif pubkey is not None:
        raise UpdateError(
            "no artifact signature supplied — refusing an unverified download")
    target_dir = Path(dest_dir) if dest_dir else _data_root() / "updates" / "dl"
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="aali-update-", suffix=".bin",
                                    dir=str(target_dir))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        final = target_dir / f"aali-{digest[:12]}.bin"
        os.replace(tmp_path, final)
    except OSError as exc:
        tmp_path.unlink(missing_errors_ok)
        raise UpdateError(f"could not save the downloaded artifact: {exc}") from exc
    log_event("download_ok", sha256_16=digest, size=len(body),
              http_status=status, transport=transport)
    return final


# ---------------------------------------------------------------------------
# [6] zip_slip_guard
# ---------------------------------------------------------------------------
def _refuse(reason: str) -> UpdateError:
    log_event("extract_refused", reason=reason)
    return UpdateError(f"zip-slip refused: {reason}")


def _check_member(member_name: str, target: Path, target_root: str) -> None:
    if not member_name or member_name in (".", "./"):
        return
    raw = member_name.replace("\\", "/")
    lowered = raw.lower()
    if "\x00" in raw:
        raise _refuse(f"NUL byte in {member_name!r}")
    if lowered.startswith("/") or re.match(r"^[a-z]:", lowered):
        raise _refuse(f"absolute path {member_name!r}")
    if lowered.startswith("//") or lowered.startswith("\\\\"):
        raise _refuse(f"UNC path {member_name!r}")
    if "%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        raise _refuse(f"percent-encoded traversal {member_name!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise _refuse(f"parent traversal {member_name!r}")
    for part in parts:
        stem = part.split(".")[0].lower()
        if stem in _WINDOWS_DEVICE_NAMES:
            raise _refuse(f"windows device name {member_name!r}")
        if part.endswith(" ") or part.endswith("."):
            raise _refuse(f"windows-illegal name {member_name!r}")
    joined = os.path.normpath(os.path.join(target_root, *parts))
    if not joined.startswith(target_root):
        raise _refuse(f"escapes the target dir {member_name!r}")
    resolved = (target / "/".join(parts)).resolve()
    if not str(resolved).startswith(str(Path(target_root))):
        raise _refuse(f"escapes the target dir {member_name!r}")


def zip_slip_guard(archive: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Raise UpdateError if ANY member of *archive* would escape *target*.

    Supports zip and tar (gztar/bz2/xz by extension). Refuses absolute paths,
    UNC, drive letters, '..' segments, %-encoded traversal, Windows device
    names, trailing-dot/space names and symlink/hardlink members (a symlink is
    a zip-slip primitive even when its own name is clean).
    """
    path = Path(archive)
    dest = Path(target)
    if not path.is_file():
        raise UpdateError(f"archive not found: {path.name}")
    dest.mkdir(parents=True, exist_ok=True)
    target_root = os.path.normpath(str(dest.resolve()))

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                _check_member(info.filename, dest, target_root)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise _refuse(f"symlink member {info.filename!r}")
        return

    try:
        is_tar = tarfile.is_tarfile(path)
    except (OSError, tarfile.TarError) as exc:
        raise UpdateError(f"archive unreadable: {exc}") from exc
    if not is_tar:
        raise UpdateError("unsupported archive format (zip or tar expected)")
    with tarfile.open(path) as tf:
        for member in tf.getmembers():
            _check_member(member.name, dest, target_root)
            if member.issym() or member.islnk() or member.isdev():
                raise _refuse(f"link/device member {member.name!r}")


# ---------------------------------------------------------------------------
# [3] stage_update
# ---------------------------------------------------------------------------
_VERSION_IN_NAME = re.compile(r"(\d+(?:\.\d+)+)")


def _version_of(archive: Path, digest: str) -> str:
    found = _VERSION_IN_NAME.search(archive.stem)
    if found:
        return found.group(1)
    return digest[:12] or "0"


def stage_update(archive_path: str | os.PathLike[str],
                 install_dir: str | os.PathLike[str], *,
                 version: str = "") -> Path:
    """Extract a VERIFIED archive into ``<install_dir>/staged/<version>/``.

    The running install is never touched: staging is reversible by deleting the
    directory, which is what makes 'download now, restart later' honest.
    """
    archive = Path(archive_path)
    root = Path(install_dir)
    if version:
        if parse_version(version) is None:
            raise UpdateError(f"unparsable version: {version!r}")
    else:
        version = _version_of(archive, _sha256_file(archive))
    staged_root = root / STAGED_DIR
    dest = staged_root / version
    if os.path.normpath(str(dest)) == os.path.normpath(str(root)):
        raise UpdateError("staging path collides with the install root")
    zip_slip_guard(archive, dest)
    staged_root.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="aali-stage-", dir=str(staged_root)))
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmpdir)
        else:
            with tarfile.open(archive) as tf:
                _safe_tar_extract(tf, tmpdir)
        (tmpdir / "VERSION").write_text(version + "\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmpdir, dest)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, ValueError) as exc:
        shutil.rmtree(tmpdir, ignore_errors=True)
        log_event("stage_failed", version=version, reason=type(exc).__name__)
        raise UpdateError(f"staging failed: {exc}") from exc
    log_event("staged", version=version)
    return dest


def _safe_tar_extract(tf: tarfile.TarFile, target: Path) -> None:
    """extractall with the per-member guard applied one last time."""
    root = os.path.normpath(str(target.resolve()))
    for member in tf.getmembers():
        _check_member(member.name, target, root)
        if member.issym() or member.islnk() or member.isdev():
            raise _refuse(f"link/device member {member.name!r}")
    try:  # py3.12 filter, older Pythons fall back to the guarded extractall
        tf.extractall(target, filter="data")
    except TypeError:  # pragma: no cover - Python < 3.12
        tf.extractall(target)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# state (version / rollback bookkeeping)
# ---------------------------------------------------------------------------
def _state_path(install_dir: str | os.PathLike[str]) -> Path:
    return Path(install_dir) / STATE_FILE


def read_state(install_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """The update state dict (empty when there is none)."""
    try:
        path = _state_path(_install_dir(install_dir))
    except UpdateError:
        return {}
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(install_dir: Path, state: dict[str, Any]) -> None:
    path = _state_path(install_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=True, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def _payload_version(directory: Path) -> str:
    marker = directory / "VERSION"
    if marker.is_file():
        try:
            return marker.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


# ---------------------------------------------------------------------------
# [4] apply_and_restart
# ---------------------------------------------------------------------------
def _default_spawn() -> None:
    """Re-exec the ALREADY-RUNNING executable, detached.

    Never runs anything out of the downloaded tree: for a frozen build this
    is the same .exe/.app the user launched, and for a source run it is the
    same interpreter with the same arguments. The caller still owns process
    exit (a UI must show its 'restart now' dialog first) — this only SPAWNS.
    """
    if os.getenv("AALI_UPDATE_NO_RESTART") == "1":
        log_event("restart_skipped", reason="kill-switch")
        return
    argv = [sys.executable] + list(sys.argv[1:]) if not getattr(sys, "frozen", False) \
        else [sys.executable]
    kwargs: dict[str, Any] = {"close_fds": True}
    if os.name == "nt":  # noqa: S603 - our own executable, argv list, no shell
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | \
            getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **kwargs)
        log_event("restart_scheduled")
    except OSError as exc:
        log_event("restart_failed", reason=type(exc).__name__)
        raise UpdateError(f"could not schedule the restart: {exc}") from exc


def apply_and_restart(staged_path: str | os.PathLike[str],
                      install_dir: str | os.PathLike[str], *,
                      restart: bool = True,
                      spawn: Callable[[], Any] | None = None) -> None:
    """Swap a staged payload in as ``current`` and schedule a restart.

    * the previous payload is MOVED (never copied) to ``previous/<version>``;
    * the staged payload becomes ``current`` and state records
      ``pending_ack=True, failures=0``;
    * ``register_startup()`` on the next launch flips pending_ack, and two
      failed launches in a row call ``rollback_if_failed()`` automatically.

    Returns None; the caller decides when to actually exit its process.
    """
    root = Path(install_dir)
    staged = Path(staged_path)
    staged_root = (root / STAGED_DIR).resolve()
    if not staged.exists():
        raise UpdateError(f"staged payload not found: {staged.name}")
    try:
        inside = staged.resolve().is_relative_to(staged_root)
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise UpdateError(
            "refusing to activate a payload that was not staged by this "
            "updater")
    version = _payload_version(staged)
    if not version:
        raise UpdateError("staged payload has no VERSION marker — refusing")

    current = root / CURRENT_DIR
    previous_root = root / PREVIOUS_DIR
    previous_root.mkdir(parents=True, exist_ok=True)
    state = read_state(root)
    old_version = _payload_version(current) or str(state.get("version", ""))

    previous_dir: Path | None = None
    if current.exists():
        previous_dir = previous_root / (old_version or "previous")
        if previous_dir.exists():
            shutil.rmtree(previous_dir, ignore_errors=True)
        os.replace(current, previous_dir)

    staged_tmp = root / f".incoming-{version}"
    if staged_tmp.exists():
        shutil.rmtree(staged_tmp, ignore_errors=True)
    os.replace(staged, staged_tmp)
    try:
        os.replace(staged_tmp, current)
    except OSError:
        os.replace(staged_tmp, staged)  # put the candidate back where it was
        raise

    _write_state(root, {
        "app": _app_name(),
        "version": version,
        "previous": _payload_version(previous_dir) if previous_dir else "",
        "previous_dir": previous_dir.name if previous_dir else "",
        "failures": 0,
        "pending_ack": True,
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    log_event("applied", version=version,
              previous=_payload_version(previous_dir) if previous_dir else "")
    if restart:
        (spawn or _default_spawn)()


def restart_now() -> None:  # pragma: no cover - replaces the process
    """Apply-then-exit helper for clients that own a real UI."""
    _default_spawn()
    sys.exit(0)


# ---------------------------------------------------------------------------
# [5] rollback_if_failed
# ---------------------------------------------------------------------------
def rollback_if_failed(install_dir: str | os.PathLike[str] | None = None,
                       *, force: bool = False) -> None:
    """Restore the retained previous payload when the new one misbehaved.

    Restores when ``failures >= MAX_FAILURES`` (the normal trigger) or when
    *force* is set (the UI's 'restore the previous version'). No previous
    payload -> nothing happens and the reason is logged honestly.
    """
    root = _install_dir(install_dir)
    state = read_state(root)
    if not state:
        return
    failures = int(state.get("failures", 0) or 0)
    if not force and failures < MAX_FAILURES:
        log_event("rollback_skipped", version=str(state.get("version", "")),
                  failures=failures, reason="below-threshold")
        return
    previous_name = str(state.get("previous_dir", "") or "")
    if not previous_name:
        previous_name = str(state.get("previous", "") or "")
    previous_dir = root / PREVIOUS_DIR / previous_name if previous_name else None
    if previous_dir is None or not previous_dir.is_dir():
        log_event("rollback_skipped", version=str(state.get("version", "")),
                  failures=failures, reason="no-previous-payload")
        return
    current = root / CURRENT_DIR
    failed_version = str(state.get("version", "") or "")
    restoring = _payload_version(previous_dir)
    if restoring and restoring == failed_version and not force:
        log_event("rollback_skipped", version=failed_version, failures=failures,
                  reason="same-version")
        return
    if current.exists():
        shutil.rmtree(current, ignore_errors=True)
    os.replace(previous_dir, current)
    _write_state(root, {
        "app": _app_name(),
        "version": restoring,
        "previous": "",
        "previous_dir": "",
        "failures": 0,
        "pending_ack": False,
        "rolled_back_from": failed_version,
        "rolled_back_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    log_event("rolled_back", version=restoring, previous=failed_version,
              failures=failures)


def register_startup(install_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Call FIRST on every app launch: counts a failed launch, rolls back at 2.

    Returns a small dict describing what happened so a client can log or toast
    ('clean' | 'first-launch' | 'failed-launch' | 'rolled-back').
    """
    root = _install_dir(install_dir)
    state = read_state(root)
    if not state:
        return {"status": "clean", "version": ""}
    if not state.get("pending_ack"):
        return {"status": "clean", "version": str(state.get("version", ""))}
    failures = int(state.get("failures", 0) or 0) + 1
    state["failures"] = failures
    _write_state(root, state)
    version = str(state.get("version", ""))
    log_event("launch_unacked", version=version, failures=failures)
    if failures >= MAX_FAILURES:
        rollback_if_failed(root)
        return {"status": "rolled-back", "version": version,
                "failures": failures}
    return {"status": "failed-launch", "version": version, "failures": failures}


def record_launch_ok(install_dir: str | os.PathLike[str] | None = None) -> None:
    """Call after a launch that reached a working UI: the update is accepted."""
    root = _install_dir(install_dir)
    state = read_state(root)
    if not state or not state.get("pending_ack"):
        return
    state["pending_ack"] = False
    state["failures"] = 0
    state["accepted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_state(root, state)
    log_event("launch_ok", version=str(state.get("version", "")))