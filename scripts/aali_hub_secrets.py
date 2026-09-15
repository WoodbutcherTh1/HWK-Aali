#!/usr/bin/env python3
"""Aali Hub production secrets — one-shot generator for the owner (Q8 prep).

Creates a secrets directory OUTSIDE the repo (default ``D:/hwk-data/aali-hub-secrets``
on the owner's machine, ``~/.aali-hub-secrets`` elsewhere) and writes:

    jwt_secret.txt        AALI_HUB_JWT_SECRET        (HS256 signing secret)
    brain_token.txt       AALI_BRAIN_TOKEN            (brain↔hub shared secret)
    master_key.txt        AALI_PROTOCOL_MASTER_KEY    (protocol HMAC master)
    update_seed_hex.txt   AALI_UPDATE_SIGNING_KEY     (Ed25519 SEED, hex)
    update_pub_hex.txt    (verification only — ship to Nodes, keep secret-side)
    hub.env               ready-to-source env block for the Hub VPS

Rules honored (AGENTS.md):
- NEVER writes inside the repository; refuses a repo path explicitly.
- Files are created owner-only (0600 on POSIX; on Windows the user profile
  directory's default ACL applies — the directory is created non-inherited
  when possible).
- Existing files are NEVER overwritten — a rerun prints and exits 0, so it
  is safe to run twice; deleting a file regenerates just that value.
- Everything is printed REDACTED (first 4 chars) — never full secrets.

Usage (hub venv has cryptography; any venv works for HMAC-only):
    .venv-hub/Scripts/python.exe scripts/aali_hub_secrets.py
    .venv-hub/Scripts/python.exe scripts/aali_hub_secrets.py --dir X:/hwk-backups/aali-hub-secrets
"""
from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "file-agent")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FILES = {
    "jwt_secret.txt": ("AALI_HUB_JWT_SECRET", 64),
    "brain_token.txt": ("AALI_BRAIN_TOKEN", 32),
    "master_key.txt": ("AALI_PROTOCOL_MASTER_KEY", 64),
}


def _default_dir() -> Path:
    if sys.platform.startswith("win"):
        data = Path(os.environ.get("HWK_DATA", "D:/hwk-data"))
        return data / "aali-hub-secrets"
    return Path.home() / ".aali-hub-secrets"


def _redact(value: str) -> str:
    return value[:4] + "…" if len(value) > 8 else "***"


def _write_owner_only(path: Path, text: str) -> bool:
    """Write once; existing files are never overwritten. True when written."""
    if path.exists():
        return False
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n")
    return True


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(_default_dir()),
                        help="secrets directory (default: D:/hwk-data/"
                             "aali-hub-secrets on Windows, ~/.aali-hub-secrets"
                             " elsewhere)")
    args = parser.parse_args()
    out_dir = Path(args.dir).expanduser().resolve()

    # hard rule: never inside the repo
    try:
        out_dir.relative_to(ROOT)
    except ValueError:
        pass
    else:
        print(f"refusing to write secrets inside the repo: {out_dir}",
              file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(out_dir, stat.S_IRWXU)  # 0700
        except OSError:
            pass

    written: list[str] = []
    skipped: list[str] = []

    # 1) plain secrets (token_hex → URL-safe, constant length)
    for name, (env, nbytes) in FILES.items():
        value = secrets.token_hex(nbytes)
        if _write_owner_only(out_dir / name, value):
            written.append(f"{name} -> {env} = {_redact(value)}…")
        else:
            skipped.append(name)

    # 2) Ed25519 update signing seed (production path when cryptography exists)
    seed = None
    try:
        from aali_hub.update_server import generate_signing_keypair
        seed, pub = generate_signing_keypair()
        if _write_owner_only(out_dir / "update_seed_hex.txt", seed):
            written.append(f"update_seed_hex.txt -> AALI_UPDATE_SIGNING_KEY "
                           f"(Ed25519 seed) = {_redact(seed)}…")
        else:
            skipped.append("update_seed_hex.txt")
        if _write_owner_only(out_dir / "update_pub_hex.txt", pub):
            written.append(f"update_pub_hex.txt -> (Node-side verify key, "
                           f"NOT an env secret) = {_redact(pub)}…")
        else:
            skipped.append("update_pub_hex.txt")
    except Exception as exc:  # cryptography missing → HMAC fallback + write it
        hmac_fallback = secrets.token_hex(64)
        if _write_owner_only(out_dir / "update_seed_hex.txt", hmac_fallback):
            written.append("update_seed_hex.txt -> AALI_UPDATE_SIGNING_KEY "
                           f"(HMAC fallback) = {_redact(hmac_fallback)}…")
        else:
            skipped.append("update_seed_hex.txt")
        print(f"note: Ed25519 unavailable ({exc}); wrote an HMAC secret "
              "instead (dev-grade — regenerate with cryptography before "
              "production)")

    # 3) ready-to-use env block for the Hub VPS (values from disk, redacted
    #    on stdout; the FULL values are in the files, never in the console)
    env_lines = [
        f"AALI_HUB_JWT_SECRET={(out_dir / 'jwt_secret.txt').read_text(encoding='utf-8').strip()}",
        f"AALI_BRAIN_TOKEN={(out_dir / 'brain_token.txt').read_text(encoding='utf-8').strip()}",
        f"AALI_PROTOCOL_MASTER_KEY={(out_dir / 'master_key.txt').read_text(encoding='utf-8').strip()}",
    ]
    if seed is None and (out_dir / "update_seed_hex.txt").exists():
        seed = (out_dir / "update_seed_hex.txt").read_text(encoding="utf-8").strip()
    if seed:
        env_lines.append(f"AALI_UPDATE_SIGNING_KEY={seed}")
    env_file = out_dir / "hub.env"
    if _write_owner_only(env_file, "\n".join(env_lines) + "\n"):
        written.append(f"hub.env ({len(env_lines)} vars, full values)")

    print(f"secrets dir: {out_dir}")
    for line in written:
        print(f"  wrote  {line}")
    for name in skipped:
        print(f"  kept   {name} (already exists — not overwritten)")
    if written:
        print("\nnext: copy hub.env to the VPS (e.g. /etc/aali-hub/env), "
              "chmod 600, and source it before uvicorn. NEVER commit these "
              "files; the repo rule (no secrets in git) is absolute.")
    else:
        print("\nall secrets already exist — nothing to do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
