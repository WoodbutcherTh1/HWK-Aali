"""Aali Node daemon — connects to the Aali Hub and executes tool calls.

Runs headless (no UI) so every shell works: bash, zsh, PowerShell, CMD::

    python -m aali_node --hub ws://127.0.0.1:8080 --token <JWT>

Contract (aali_hub/ws_gateway.py):
- hello {role:"node", node_id, session_id, token:<JWT>} — the Hub verifies
  the JWT and derives this leg's signing key from it (derive_session_key).
- inbound tool_call messages are verified against that leg key, executed
  inside aali_node.sandbox.SecureSandbox, and answered with tool_result.
- heartbeat: TYPE_PING every HEARTBEAT_INTERVAL_SEC, answered by TYPE_PONG.

The asyncio transport is lazy-imported so the sandbox stays unit-testable in
any venv (the hub venv has websockets; the training venv does not need them).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import secrets
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # direct-script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aali_node import sandbox as SB  # noqa: E402
from file_agent import protocol as P  # noqa: E402

__all__ = ["NodeSession", "run_node"]


class NodeSession:
    """One node connection's pure message handling (testable without IO).

    ``handle_message`` verifies an inbound message against the leg key and
    returns (send_back, sandbox_result); the async transport owns sockets.
    """

    def __init__(self, sandbox: SB.SecureSandbox, session_id: str,
                 node_id: str, token: str) -> None:
        # The token is REQUIRED at construction: the leg key is derived from
        # it, and derive_session_key refuses empty secrets — a tokenless
        # session would silently fail to verify anything.
        if not isinstance(token, str) or not token:
            raise ValueError("a non-empty node token is required")
        self.sandbox = sandbox
        self.session_id = session_id
        self.node_id = node_id
        self.leg_key = P.derive_session_key(token, session_id)
        self.stats = {"executed": 0, "denied": 0, "errors": 0,
                      "dropped": 0, "pongs_sent": 0}

    def hello(self) -> dict[str, Any]:
        return {
            "type": "hello", "role": "node", "node_id": self.node_id,
            "session_id": self.session_id, "v": P.PROTOCOL_VERSION,
        }

    def bind_token(self, token: str) -> None:
        """Re-bind the leg key (token refresh); empty tokens are refused."""
        if not isinstance(token, str) or not token:
            raise ValueError("a non-empty node token is required")
        self.leg_key = P.derive_session_key(token, self.session_id)

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Verify + execute one inbound message; unsigned reply or None."""
        try:
            P.parse_message(message, self.leg_key)
        except P.ProtocolError:
            self.stats["dropped"] += 1
            return None

        msg_type = message.get("type")
        if msg_type == P.TYPE_PING:
            self.stats["pongs_sent"] += 1
            return P.make_pong(message)
        if msg_type == P.TYPE_TOOL_CALL:
            result = self.sandbox.execute(message)
            status = str(result.get("status"))
            if status == "ok":
                self.stats["executed"] += 1
            elif status == "denied":
                self.stats["denied"] += 1
            else:
                self.stats["errors"] += 1
            return result
        self.stats["dropped"] += 1
        return None


def run_node(hub_url: str, token: str, workspace_root: str | Path, *,
             allow_commands: bool = True,
             session_id: str | None = None,
             stop: "threading.Event | None" = None,
             native_confirm: bool = False) -> int:
    """Connect to the Hub, serve tool calls until interrupted. Exit code.

    ``stop``: optional threading.Event — setting it ends the receive loop
    cleanly (used by embedders/tests that run this in a thread).
    ``native_confirm``: pop native OS dialogs for dangerous calls (the GUI
    shell's mode); default is the console y/N prompt.
    """
    # wire the sandbox FIRST so configuration errors surface before the
    # transport check (a misconfigured node must fail fast, not report a
    # missing websocket package instead of its real problem)
    session_id = session_id or uuid.uuid4().hex
    node_id = os.getenv("AALI_NODE_ID") or f"node-{secrets.token_hex(4)}"
    from aali_node.confirm import make_confirm_hook
    box = SB.SecureSandbox(workspace_root, allow_commands=allow_commands,
                           confirm_hook=make_confirm_hook(
                               use_native=native_confirm))
    session = NodeSession(box, session_id, node_id, token)

    try:
        import websockets  # lazy: keeps sandbox unit-testable everywhere
    except ImportError:
        print("aali_node: the 'websockets' package is required for the "
              "daemon (pip install websockets). The sandbox itself works "
              "without it.", file=sys.stderr)
        return 2

    ws_url = hub_url.rstrip("/") + "/ws/node"

    async def _run() -> int:
        import websockets

        async with websockets.connect(ws_url) as ws:
            hello = session.hello()
            hello["token"] = token
            await ws.send(json.dumps(hello))
            print(f"aali_node: connected to {ws_url} "
                  f"(node {node_id}, session {session_id[:8]}…, "
                  f"workspace {box.root})")

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(P.HEARTBEAT_INTERVAL_SEC)
                    with contextlib.suppress(Exception):
                        await ws.send(json.dumps(
                            P.sign_message(P.make_ping(), session.leg_key)))

            hb = asyncio.create_task(heartbeat())
            try:
                while stop is None or not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(),
                                                     timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        message = json.loads(raw)
                    except (TypeError, ValueError):
                        session.stats["dropped"] += 1
                        continue
                    try:
                        reply = session.handle_message(message)
                    except Exception as exc:
                        # Lesson (live test): a handler crash used to kill the
                        # whole daemon mid-connection. Report, count, survive.
                        session.stats["errors"] += 1
                        print(f"aali_node: handler error: "
                              f"{type(exc).__name__}: {exc}", file=sys.stderr)
                        continue
                    if reply is not None:
                        await ws.send(json.dumps(
                            P.sign_message(reply, session.leg_key)))
            finally:
                hb.cancel()
        return 0

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        print("aali_node: stopped by user")
        return 0
    except Exception as exc:
        print(f"aali_node: connection failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aali_node",
        description="Aali Node — user-side execution daemon for Aali Cloud",
    )
    parser.add_argument("--hub", default=os.getenv("AALI_HUB_URL",
                                                   "ws://127.0.0.1:8080"),
                        help="Hub WebSocket base URL (default env "
                             "AALI_HUB_URL or ws://127.0.0.1:8080)")
    parser.add_argument("--token", default=os.getenv("AALI_NODE_TOKEN", ""),
                        help="access JWT from the Hub login (or env "
                             "AALI_NODE_TOKEN)")
    parser.add_argument("--workspace",
                        default=os.getenv("AALI_NODE_WORKSPACE",
                                          str(Path.home() / "AaliWorkspace")),
                        help="the ONLY directory this Node may touch")
    parser.add_argument("--no-commands", action="store_true",
                        help="disable run_command entirely on this Node")
    parser.add_argument("--native-confirm", action="store_true",
                        help="ask via native OS dialogs instead of the "
                             "console (GUI-shell mode)")
    args = parser.parse_args(argv)

    if not args.token:
        print("aali_node: no token — log in via the Hub "
              "(POST /auth/login) and pass --token or set AALI_NODE_TOKEN",
              file=sys.stderr)
        return 2
    if len(args.token) > 4096:
        print("aali_node: token unreasonably long", file=sys.stderr)
        return 2

    return run_node(args.hub, args.token, args.workspace,
                    allow_commands=not args.no_commands,
                    native_confirm=args.native_confirm)


if __name__ == "__main__":
    sys.exit(main())
