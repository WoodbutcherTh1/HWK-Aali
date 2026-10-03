"""آلي ستوديو — the version triple every update surface reads.

Three values, one module, so the status bar, the settings dialog, the About
box, the CLI-facing ``--version`` and the PUBLISH scripts can never disagree
about what "1.0.0" means.

``UPDATE_PUBKEY_HEX`` is the Ed25519 **public** verification key (64 hex). It
is baked in at BUILD time — that is the whole point: a client that fetched its
key from the hub would trust whatever the hub served. Resolution order:

1. ``UPDATE_PUBKEY_HEX`` below, written by the build/publish scripts,
2. ``AALI_STUDIO_UPDATE_PUBKEY`` / ``AALI_UPDATE_PUBLIC_KEY`` (a developer
   machine, or the owner's own hub),
3. ``<data root>/aali-hub-secrets/update_pub_hex.txt`` (the owner's PC),
4. empty — and then :func:`public_key` returns None, which makes the updater
   REFUSE every manifest. That is the honest failure: no key means no
   updates, never an unsigned "update available".
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "1.0.0"
BUILD_DATE = "2026-10-03"
COMMIT = "auto-generated"

#: Filled by scripts/publish_studio.bat (or a manual edit). Public key only —
#: the seed must never enter this repo or a client binary.
UPDATE_PUBKEY_HEX = ""

APP_ID = "studio"
#: The hub artifact key for this platform. The shared module maps the host to
#: 'win' / 'macos' / 'linux'; this is only the fallback for odd platforms.
PLATFORM = ""

_KEY_ENV = ("AALI_STUDIO_UPDATE_PUBKEY", "AALI_UPDATE_PUBLIC_KEY")
_KEY_FILE = Path("D:/hwk-data/aali-hub-secrets/update_pub_hex.txt")

_CACHE: list = []


def version_string() -> str:
    return f"v{__version__}"


def build_info() -> dict[str, str]:
    """What /api/about and the status bar report."""
    return {
        "version": __version__,
        "version_string": version_string(),
        "build_date": BUILD_DATE,
        "commit": COMMIT,
        "app": APP_ID,
    }


def public_key_hex() -> str:
    """The verification key, resolved fresh (never cached across a change)."""
    if UPDATE_PUBKEY_HEX.strip():
        return UPDATE_PUBKEY_HEX.strip()
    for name in _KEY_ENV:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    try:
        if _KEY_FILE.is_file():
            return _KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def public_key():
    """An Ed25519PublicKey, or None when no key is provisioned yet.

    A malformed key is a refusal (``shared.updater.UpdateError``), never a
    silent None — a typo in a build script must not quietly disable
    verification.
    """
    import sys
    here = Path(__file__).resolve().parent
    repo = here.parents[1]
    for extra in (repo / "scripts", repo / "file-agent", here):
        if extra.is_dir() and str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    from shared.updater import pubkey_from_hex
    hex_text = public_key_hex()
    if not hex_text:
        return None
    return pubkey_from_hex(hex_text)


if __name__ == "__main__":  # `python version.py` — what the build scripts read
    import json
    print(json.dumps({**build_info(), "public_key": bool(public_key_hex())},
                     ensure_ascii=False, indent=2))