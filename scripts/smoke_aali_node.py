#!/usr/bin/env python3
"""LIVE smoke test for the Aali Node against a REAL Hub — operator runbook.

Everything is real and local, nothing mocked (this is the post-build check
the owner/CI runs before shipping a Node):

  real uvicorn Hub (subprocess)  ←—  owner seeded over HTTP
  real daemon (subprocess, --update-hub)  →  stages verified 0.2.0
  real brain leg (websockets)  →  tool_dispatch(write_file)  →  ok relayed
  aali_node --activate-update (detached worker)  →  install swapped to 0.2.0
  restore: activate 0.1.0 from .aali_prev (or just re-run in a temp dir)

Run (hub venv):
    .venv-hub/Scripts/python.exe scripts/smoke_aali_node.py
    .venv-hub/Scripts/python.exe scripts/smoke_aali_node.py --keep

Exit 0 = all phases green. The default install root is a temp dir, so a
smoke run can never damage the developer's real tree (--keep prints it).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "file-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

# Windows consoles are cp1252 — force UTF-8 so ✓/✗/— never crash the run
# (the mission_clock lesson, learned the hard way there too).
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

JWT_SECRET = "smoke-jwt-secret-0123456789abcdef-0123"
BRAIN_TOKEN = "smoke-brain-token-0123456789abcdef"
MASTER_KEY = "smoke-master-key-0123456789abcdef-0123456789"
SIGNING_KEY = "smoke-signing-key-0123456789abcdef-0123456789"

PASS: list[str] = []
FAIL: list[str] = []


def _phase(name: str) -> None:
    print(f"\n== {name} " + "=" * max(0, 60 - len(name)))


def _ok(name: str, detail: str = "") -> None:
    PASS.append(name)
    print(f"  PASS {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str = "") -> None:
    FAIL.append(name)
    print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_json(url: str, payload: dict | None = None,
               token: str | None = None,
               raw: bytes | None = None) -> tuple[int, dict | bytes]:
    body = raw if raw is not None else (
        json.dumps(payload).encode() if payload is not None else None)
    headers = {"Content-Type":
               "application/octet-stream" if raw is not None
               else "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(data) if "json" in ctype
                                 else data)
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def _wait_health(base: str, deadline_sec: float = 15.0) -> bool:
    deadline = time.monotonic() + deadline_sec
    while time.monotonic() < deadline:
        try:
            code, _ = _http_json(f"{base}/health")
            if code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _make_node_install(dest: Path, version: str) -> None:
    """Copy the real aali_node + protocol/redaction into a minimal install.

    file_agent gets a MINIMAL __init__.py: the repo's real one imports
    file_tools (and through it requests/Flask) — the node only needs
    protocol + redaction (the same stdlib-only rule the package follows).
    """
    (dest / "aali_node").mkdir(parents=True, exist_ok=True)
    (dest / "file_agent").mkdir(parents=True, exist_ok=True)
    (dest / "aali_hub").mkdir(parents=True, exist_ok=True)
    for name in ("sandbox.py", "daemon.py", "confirm.py",
                 "updater.py", "shell.py", "activate.py", "__main__.py"):
        shutil.copy2(ROOT / "aali_node" / name, dest / "aali_node" / name)
    # the updater verifies manifests through aali_hub.update_server
    # (stdlib-only, cryptography optional) — a real Node ships it too
    (dest / "aali_hub" / "__init__.py").write_text(
        '"""smoke: hub helpers shipped with the node"""\n', encoding="utf-8")
    shutil.copy2(ROOT / "aali_hub" / "update_server.py",
                 dest / "aali_hub" / "update_server.py")
    (dest / "aali_node" / "__init__.py").write_text(
        f'"""smoke install"""\n__version__ = "{version}"\n',
        encoding="utf-8")
    (dest / "file_agent" / "__init__.py").write_text(
        '"""minimal smoke file_agent (protocol + redaction + file_tools)"""\n',
        encoding="utf-8")
    for name in ("protocol.py", "redaction.py", "file_tools.py"):
        shutil.copy2(ROOT / "file-agent" / "file_agent" / name,
                     dest / "file_agent" / name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true",
                        help="keep the temp install for inspection")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="aali-node-smoke-"))
    print(f"smoke root: {tmp}" + ("  (--keep)" if args.keep else ""))

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    hub_db = tmp / "hub.db"
    env = dict(os.environ,
               AALI_HUB_HOST="127.0.0.1", AALI_HUB_PORT=str(port),
               AALI_HUB_JWT_SECRET=JWT_SECRET, AALI_BRAIN_TOKEN=BRAIN_TOKEN,
               AALI_PROTOCOL_MASTER_KEY=MASTER_KEY,
               AALI_UPDATE_SIGNING_KEY=SIGNING_KEY,
               AALI_HUB_DB=str(hub_db),
               AALI_HUB_LOG_DIR=str(tmp / "hub_logs"),
               AALI_UPDATE_DIR=str(tmp / "updates"),
               AALI_BRAIN_URL="http://127.0.0.1:1",
               PYTHONPATH=f"{ROOT};{ROOT / 'file-agent'}")
    py = sys.executable

    hub_proc = None
    daemon_proc = None
    daemon_log = None
    try:
        # ---- phase 1: real hub ------------------------------------------------
        _phase("phase 1 — real hub")
        hub_proc = subprocess.Popen(
            [py, "-c",
             "import sys; sys.path.insert(0, r'%s'); "
             "from aali_hub.config import load_config; "
             "from aali_hub.main import create_app; "
             "import uvicorn; "
             "cfg = load_config(); "
             "uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, "
             "log_level='warning')" % ROOT],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_health(base):
            _fail("hub health", "server never became healthy")
            return _finish(args, tmp)
        _ok("hub health", base)

        # owner + user, real registration/login
        code, body = _http_json(f"{base}/api/auth/register",
                                {"email": "smoke@example.com",
                                 "password": "smokepass123"})
        seed = tmp / "seed_owner.py"
        seed.write_text(
            "import sys; sys.path.insert(0, r'%s');\n"
            "from aali_hub.users_db import UsersDB;\n"
            "db = UsersDB(r'%s');\n"
            "db.create_user('owner@smoke.local', 'ownerpass123',\n"
            "               role='owner', email_verified=True)\n"
            "print('owner seeded')\n" % (ROOT, hub_db))
        r = subprocess.run([py, str(seed)], capture_output=True, text=True,
                           timeout=30)
        if "owner seeded" not in r.stdout:
            _fail("seed owner", (r.stderr or r.stdout).strip()[:1200])
            return _finish(args, tmp)
        _ok("seed owner")

        code, body = _http_json(f"{base}/api/auth/login",
                                {"email": "smoke@example.com",
                                 "password": "smokepass123"})
        user_token = body.get("token", "") if code == 200 else ""
        # the dispatch's user_id MUST equal the node's JWT subject —
        # the broker routes node_for_user(user_id) by that claim
        (code, me) = _http_json(f"{base}/api/auth/me", token=user_token)
        user_id = str(me.get("user", {}).get("id", "")) if code == 200 else ""
        (code, body) = _http_json(f"{base}/api/auth/login",
                                  {"email": "owner@smoke.local",
                                   "password": "ownerpass123"})
        owner_token = body.get("token", "") if code == 200 else ""
        if user_token and owner_token and user_id:
            _ok("register + login", f"user + owner JWTs minted (id {user_id[:8]}…)")
        else:
            _fail("register + login", f"user={bool(user_token)} "
                                      f"owner={bool(owner_token)}")
            return _finish(args, tmp)

        # publish a REAL 0.2.0 artifact through the admin surface
        ver = "0.2.0"
        buf = io.BytesIO()
        stage_zip = tmp / f"artifact-{ver}.zip"
        _make_node_install(tmp / f"artifact-src-{ver}", ver)
        with zipfile.ZipFile(buf, "w") as zf:
            for f in sorted((tmp / f"artifact-src-{ver}").rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(tmp / f"artifact-src-{ver}"))
        stage_zip.write_bytes(buf.getvalue())
        code, body = _http_json(
            f"{base}/api/admin/updates/publish?version={ver}&min_version=0",
            token=owner_token, raw=stage_zip.read_bytes())
        if code == 200:
            _ok("publish 0.2.0", "signed artifact accepted")
        else:
            _fail("publish 0.2.0", f"HTTP {code}")
            return _finish(args, tmp)

        # ---- phase 2: real daemon + update staging ---------------------------
        _phase("phase 2 — real daemon stages the update")
        install = tmp / "install"
        _make_node_install(install, "0.1.0")
        workspace = tmp / "ws"
        workspace.mkdir()
        daemon_log = open(tmp / "daemon.log", "w", encoding="utf-8")  # noqa: SIM115
        daemon_proc = subprocess.Popen(
            [py, "-m", "aali_node",
             "--hub", f"ws://127.0.0.1:{port}",
             "--token", user_token,
             "--workspace", str(workspace),
             "--session-id", "smokesess",
             "--update-hub", base, "--update-key", SIGNING_KEY],
            cwd=str(install),
            env=dict(env, PYTHONPATH=str(install)),
            stdout=daemon_log, stderr=subprocess.STDOUT)
        staged = install / "staged" / ver / "aali_node" / "__init__.py"
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and not staged.is_file():
            time.sleep(0.2)
        if staged.is_file():
            _ok("update staged", f"{staged.parent.parent} (verified)")
        else:
            _fail("update staged", "daemon never staged 0.2.0 in time")
            return _finish(args, tmp)

        # ---- phase 3: real brain leg dispatch ---------------------------------
        _phase("phase 3 — brain dispatch → node sandbox")
        sys.path.insert(0, str(ROOT))
        import asyncio

        import websockets

        from file_agent import protocol as P
        from aali_hub.ws_gateway import brain_leg_key

        brain_key = brain_leg_key(BRAIN_TOKEN)

        async def _dispatch() -> dict:
            async with websockets.connect(
                    f"ws://127.0.0.1:{port}/ws/brain") as ws:
                await ws.send(json.dumps({"type": "hello", "role": "brain",
                                          "token": BRAIN_TOKEN}))
                call = P.make_tool_call("write_file",
                                        {"path": "smoke.txt",
                                         "content": "smoke live"},
                                        timeout_sec=15)
                call["session_id"] = "smokesess"
                dispatch = P.sign_message(
                    P.make_tool_dispatch(user_id, call), brain_key)
                deadline = time.monotonic() + 25.0
                last_send = 0.0
                while time.monotonic() < deadline:
                    if time.monotonic() - last_send > 0.5:
                        await ws.send(json.dumps(dispatch))
                        last_send = time.monotonic()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") == "pong":
                        continue
                    P.verify_message(msg, brain_key)
                    if msg.get("type") == P.TYPE_TOOL_RESULT \
                            and msg.get("id") == call["id"]:
                        return msg
                return {}

        result = asyncio.run(_dispatch())
        target = workspace / "smoke.txt"
        # the wire tool_result carries status/result at TOP level (protocol
        # v1.0.0) — the old payload["status"] read always read None and
        # failed the phase even on success
        if result.get("status") == "ok" \
                and target.read_text(encoding="utf-8") == "smoke live":
            _ok("tool dispatch live", f"{target.name} written by the node")
        else:
            # diagnose WHERE the chain dropped it: the hub's broker counters
            # (dispatched / node_offline / inbound_dropped) + whether the
            # sandbox file appeared (node executed but the reply was lost).
            _code, metrics = _http_json(f"{base}/metrics")
            metrics_text = (metrics.decode("utf-8", errors="replace")
                            if isinstance(metrics, bytes) else str(metrics))
            wrote = target.exists() \
                and target.read_text(encoding="utf-8") == "smoke live"
            _fail("tool dispatch live",
                  f"result={result} sandbox_wrote_file={wrote} "
                  f"broker=[{metrics_text.strip().replace(chr(10), '; ')}]")
        # the node leg the broker used is the smoke session derived from the
        # user token; the result status proves the whole signed chain.

        # ---- phase 4: activation swap ----------------------------------------
        _phase("phase 4 — activation swap (staged 0.2.0 → install)")
        from aali_node.activate import activate_update, install_version
        new_version = activate_update(install / "staged" / ver, install)
        if install_version(install) == "0.2.0" \
                and (install / "new_only.txt").exists() is False \
                and (install / "aali_node" / "__init__.py").is_file():
            _ok("activation swap", f"install now {new_version}, backup at "
                                   f".aali_prev")
        else:
            _fail("activation swap", f"install version now "
                                     f"{install_version(install)}")

        return _finish(args, tmp)
    finally:
        for proc in (daemon_proc, hub_proc):
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if daemon_log is not None:
            try:
                daemon_log.close()
            except OSError:
                pass


def _finish(args, tmp: Path) -> int:
    print("\n== summary " + "=" * 51)
    for name in PASS:
        print(f"  ✓ {name}")
    for name in FAIL:
        print(f"  ✗ {name}")
    total = len(PASS) + len(FAIL)
    print(f"\n{len(PASS)}/{total} phases green"
          + ("" if FAIL else "  — SMOKE OK"))
    if not args.keep and not FAIL:
        shutil.rmtree(tmp, ignore_errors=True)
        print("(temp smoke root removed)")
    else:
        print(f"(smoke root kept: {tmp})")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
