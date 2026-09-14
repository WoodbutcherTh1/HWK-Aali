"""LIVE end-to-end: real Hub server ↔ real Aali Node daemon.

Runs only where fastapi+uvicorn+websockets exist (the hub venv — same place
the hub HTTP tests live). Everything is loopback on an ephemeral port:

    brain client ── dispatch ─▶ Hub ─▶ node daemon ─▶ SecureSandbox
        ▲                                          │
        └────────── signed tool_result ◀───────────┘

The node writes a real file through the REAL file_tools inside its temp
workspace; the brain client reads the relayed, leg-verified tool_result.

Test-harness notes (lessons from the first hang):
- the daemon runs in a daemonized THREAD (never joined by pytest) with a
  stop Event — asyncio.to_thread would hang the event loop forever because
  a cancelling thread cannot exit its receive loop;
- the brain leg RE-SENDS its dispatch while polling: the node may not have
  finished its handshake when the first dispatch goes out.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

# ---- venv gate: hub-venv only (same policy as the hub HTTP tests) -----------
if importlib.util.find_spec("aali_hub") is None or \
        importlib.util.find_spec("fastapi") is None:
    pytest.skip("hub venv only (fastapi/uvicorn/websockets required)",
                allow_module_level=True)

import uvicorn  # noqa: E402
import websockets  # noqa: E402

from aali_hub import auth as hub_auth  # noqa: E402
from aali_hub.config import HubConfig  # noqa: E402
from aali_hub.main import create_app  # noqa: E402
from aali_hub.ws_gateway import brain_leg_key  # noqa: E402
from file_agent import protocol as P  # noqa: E402

JWT_SECRET = "live-e2e-jwt-secret"
BRAIN_TOKEN = "live-e2e-brain-token"


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def hub_server():
    """A real Hub (uvicorn) on 127.0.0.1:<ephemeral>, in a daemon thread."""
    port = _free_port()
    config = HubConfig(
        host="127.0.0.1", port=port,
        jwt_secret=JWT_SECRET, brain_token=BRAIN_TOKEN,
        master_key="live-e2e-protocol-master",
        dev_mode=False,
    )
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("hub server did not start within 10s")
    yield f"ws://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5.0)


def test_node_daemon_executes_brain_dispatch_live(hub_server, tmp_path):
    result, workspace = _run_scenario(
        hub_server, tmp_path, tool="write_file",
        args={"path": "live.txt", "content": "written live"})
    assert result["status"] == "ok"
    assert (workspace / "live.txt").read_text(
        encoding="utf-8") == "written live"


def test_live_dispatch_of_denied_tool_reports_denial(hub_server, tmp_path):
    result, _ = _run_scenario(
        hub_server, tmp_path, tool="web_search", args={"query": "x"})
    assert result["status"] == "denied"


def _run_scenario(hub_url: str, tmp_path: Path, *, tool: str,
                  args: dict) -> tuple[dict, Path]:
    """Full live loop: daemon thread + brain leg; returns (result, workspace)."""
    from aali_node.daemon import run_node

    workspace = tmp_path / "ws"
    jwt = hub_auth.mint_token("user-live", "user", JWT_SECRET, ttl_sec=3600)
    stop = threading.Event()

    def _daemon() -> None:
        run_node(hub_url, jwt, workspace, allow_commands=True,
                 session_id="livesess", stop=stop)

    node_thread = threading.Thread(target=_daemon, daemon=True)
    node_thread.start()
    try:
        result = _brain_leg(hub_url, "user-live", "livesess", tool, args)
        return result, workspace
    finally:
        stop.set()
        node_thread.join(timeout=5.0)


def _brain_leg(hub_url: str, user_id: str, session_id: str,
               tool: str, args: dict, *, wait_sec: float = 20.0) -> dict:
    """Act as the brain: dispatch until the relayed tool_result arrives."""
    import asyncio

    async def _run() -> dict:
        async with websockets.connect(hub_url + "/ws/brain") as ws:
            await ws.send(json.dumps({"type": "hello", "role": "brain",
                                      "token": BRAIN_TOKEN}))
            call = P.make_tool_call(tool, args, timeout_sec=15)
            call["session_id"] = session_id
            dispatch = P.sign_message(
                P.make_tool_dispatch(user_id, call),
                brain_leg_key(BRAIN_TOKEN))
            deadline = time.monotonic() + wait_sec
            last_send = 0.0
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now - last_send > 0.5:  # re-send until the node answers
                    await ws.send(json.dumps(dispatch))
                    last_send = now
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "pong":
                    continue
                P.verify_message(msg, brain_leg_key(BRAIN_TOKEN))
                if msg.get("type") == P.TYPE_TOOL_RESULT \
                        and msg.get("id") == call["id"]:
                    return msg
            raise AssertionError("no tool_result within the wait window")

    return asyncio.run(_run())
