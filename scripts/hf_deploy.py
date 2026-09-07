"""One-shot Aali deployment to a Hugging Face Space (Docker SDK).

Logs in with the owner's credentials (passed via HF_EMAIL / HF_PASSWORD env —
NEVER hard-coded), creates a scoped access token, creates the Space, and
uploads ONLY the safe files: server code + built web app. Private data
(sessions.jsonl, logs/, agent_workspace/, __pycache__) never leaves the PC.

Fallback: if the password flow is blocked, prints exact manual steps.

    HF_EMAIL=... HF_PASSWORD=... python scripts/hf_deploy.py [--space aali] [--private]
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://huggingface.co"

# Server code that ships to the Space. Everything else under file-agent/
# (sessions.jsonl, logs/, agent_workspace/, __pycache__) stays local forever.
SERVER_FILES = [
    "file-agent/app.py",
    "file-agent/agent_loop.py",
    "file-agent/agent_log.py",
    "file-agent/providers.py",
    "file-agent/tools.py",
    "file-agent/requirements.txt",
    "file-agent/pyproject.toml",
]


def login_session(email: str, password: str) -> requests.Session:
    """Password login against huggingface.co; returns the authenticated session."""
    session = requests.Session()
    session.headers["User-Agent"] = "aali-deploy/1.0"
    page = session.get(f"{BASE}/login", timeout=30)
    page.raise_for_status()
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
    payload = {"username": email, "password": password}
    if match:
        payload["csrf_token"] = match.group(1)
    response = session.post(f"{BASE}/login", data=payload, timeout=30, allow_redirects=True)
    response.raise_for_status()
    who = session.get(f"{BASE}/api/whoami-v2", timeout=30)
    if who.status_code != 200 or "name" not in who.json():
        raise RuntimeError("login did not produce a valid session (wrong password or bot protection)")
    return session


def create_write_token(session: requests.Session, name: str = "aali-deploy") -> str:
    """Create a scoped access token through the web APIs using the session."""
    # Modern JSON API first, form fallback second.
    response = session.post(f"{BASE}/api/tokens", json={"name": name, "role": "write"}, timeout=30)
    if response.ok:
        token = response.json().get("token") or response.json().get("accessToken")
        if token:
            return token
    page = session.get(f"{BASE}/settings/tokens", timeout=30)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
    form = {"name": name, "role": "write", "kind": "fineGrained"}
    if match:
        form["csrf_token"] = match.group(1)
    response = session.post(f"{BASE}/settings/tokens/add", data=form, timeout=30, allow_redirects=True)
    if response.ok:
        reveal = re.search(r'hf_[A-Za-z0-9]{30,}', response.text)
        if reveal:
            return reveal.group(0)
    raise RuntimeError("could not create an access token automatically")


def stage_payload() -> Path:
    """Copy only the shippable files into a clean staging dir."""
    staging = Path(tempfile.mkdtemp(prefix="aali-space-"))
    (staging / "file-agent" / "hwk_model").mkdir(parents=True)
    (staging / "file-agent" / "file_agent").mkdir(parents=True)
    for rel in SERVER_FILES:
        source = ROOT / rel
        shutil.copy2(source, staging / rel)
    for source in (ROOT / "file-agent" / "file_agent").glob("*.py"):
        shutil.copy2(source, staging / "file-agent" / "file_agent" / source.name)
    for source in (ROOT / "file-agent" / "hwk_model").glob("*.py"):
        shutil.copy2(source, staging / "file-agent" / "hwk_model" / source.name)
    shutil.copytree(ROOT / "web" / "dist", staging / "web" / "dist")
    shutil.copy2(ROOT / "deploy" / "hf_space" / "Dockerfile", staging / "Dockerfile")
    shutil.copy2(ROOT / "deploy" / "hf_space" / "README.md", staging / "README.md")
    return staging


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--space", default="aali")
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()

    email = os.getenv("HF_EMAIL", "").strip()
    password = os.getenv("HF_PASSWORD", "")
    token_env = os.getenv("HF_TOKEN", "").strip()
    if not token_env and (not email or not password):
        print("HF_TOKEN (or HF_EMAIL/HF_PASSWORD) env required")
        return 2

    username: str
    token: str
    if token_env:
        print("[1/5] using the provided access token…", flush=True)
        from huggingface_hub import HfApi

        probe = HfApi(token=token_env)
        try:
            username = probe.whoami()["name"]
        except Exception as exc:  # noqa: BLE001
            print(f"TOKEN REJECTED: {exc}")
            return 3
        token = token_env
        print(f"    authenticated as: {username}")
    else:
        print("[1/5] logging in…", flush=True)
        try:
            session = login_session(email, password)
        except Exception as exc:  # noqa: BLE001
            print(f"PASSWORD LOGIN FAILED: {exc}")
            print("Manual path (2 minutes): log in at huggingface.co → Settings →")
            print("Access Tokens → New token (Write) → paste it to me and I continue.")
            return 3
        username = session.get(f"{BASE}/api/whoami-v2", timeout=30).json()["name"]
        print(f"    logged in as: {username}")

        print("[2/5] creating a scoped access token…", flush=True)
        try:
            token = create_write_token(session)
        except Exception as exc:  # noqa: BLE001
            print(f"TOKEN CREATION FAILED: {exc}")
            print("Manual path: huggingface.co/settings/tokens → New token (Write).")
            return 3
        print("    token created (kept in memory only)")

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    repo_id = f"{username}/{args.space}"
    print(f"[3/5] creating Space {repo_id} (docker, {'private' if args.private else 'public'})…", flush=True)
    url = api.create_repo(
        repo_id=repo_id,
        repo_type="space",
        space_sdk="docker",
        private=args.private,
        exist_ok=True,
    )
    print(f"    {url}")

    api_key = "aali-" + secrets.token_urlsafe(18)
    try:
        api.add_space_secret(repo_id=repo_id, key="AALI_API_KEY", value=api_key)
        print("[4/5] AALI_API_KEY secret set (multi-user gate ON from day one)")
    except Exception as exc:  # noqa: BLE001
        print(f"[4/5] could not set secret automatically ({exc}) — set it in Space settings later")

    print("[5/5] staging + uploading server code and web app…", flush=True)
    staging = stage_payload()
    try:
        api.upload_folder(
            folder_path=str(staging),
            repo_id=repo_id,
            repo_type="space",
            commit_message="Aali server: API + SSE + web app",
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print("    uploaded (private data excluded: sessions.jsonl, logs/, workspace)")

    print("\n=== DEPLOYMENT SUBMITTED ===")
    print(f"Space:    https://huggingface.co/spaces/{repo_id}")
    print(f"Web app:  https://{username}-{args.space}.hf.space/ui/")
    print(f"API:      https://{username}-{args.space}.hf.space/api/ask  (X-API-Key: the AALI_API_KEY secret)")
    print(f"AALI_API_KEY (save it, give it to your clients): {api_key}")
    print("Build takes ~3-5 min. NOTE: the Space has no model brain yet —")
    print("add an OPENROUTER_API_KEY secret (or wait for the re-SFT upload) to bring it alive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
