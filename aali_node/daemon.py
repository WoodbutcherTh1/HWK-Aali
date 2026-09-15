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
import time
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
             native_confirm: bool = False,
             update_hub: str | None = None,
             update_key: str | None = None,
             status: "dict[str, Any] | None" = None,
             install_root: "str | Path | None" = None) -> int:
    """Connect to the Hub, serve tool calls until interrupted. Exit code.

    ``stop``: optional threading.Event — setting it ends the receive loop
    cleanly (used by embedders/tests that run this in a thread).
    ``native_confirm``: pop native OS dialogs for dangerous calls (the GUI
    shell's mode); default is the console y/N prompt.
    ``update_hub``/``update_key``: opt-in signed auto-update check against
    the Hub's /updates surface BEFORE connecting. A failed check never
    keeps the Node from serving; a verified NEWER artifact is staged under
    ``<workspace's install root>/staged/<version>`` for a later swap.
    ``status``: optional dict the daemon keeps updated for embedders (the
    GUI shell polls it). CONTENT-FREE by design: state, hub_url, node_id,
    session_id, workspace, allow_commands, native_confirm, stats counters,
    connected_since, last_event / last_error strings. Never tool names,
    paths, or message content.
    ``install_root``: where THIS Node is installed (updates stage under
    ``<install_root>/staged/<version>/`` and --activate-update swaps
    there). Defaults to the workspace for backward compatibility; main()
    computes the real one (frozen exe dir / package parent).
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

    def _publish(**fields: Any) -> None:
        # status dict writes are atomic per key under the GIL; the shell's
        # poller folds snapshots — a torn read is cosmetic, never harmful.
        if status is None:
            return
        with contextlib.suppress(Exception):
            status.update(fields)

    def _publish_stats() -> None:
        _publish(stats=dict(session.stats))

    _publish(state="starting", hub_url=hub_url, node_id=node_id,
             session_id=session_id, workspace=str(workspace_root),
             allow_commands=allow_commands, native_confirm=native_confirm,
             stats=dict(session.stats), connected_since=None,
             last_event="", last_error="")

    if update_hub:
        # transport-independent: a Node that cannot serve WebSockets can
        # still be told (via its log) that a verified update is waiting
        _run_update_check(update_hub, token, update_key or token,
                          install_root if install_root is not None
                          else workspace_root, status=status)

    try:
        import websockets  # lazy: keeps sandbox unit-testable everywhere
    except ImportError:
        print("aali_node: the 'websockets' package is required for the "
              "daemon (pip install websockets). The sandbox itself works "
              "without it.", file=sys.stderr)
        _publish(state="error", last_error="websockets package missing")
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
            _publish(state="connected", connected_since=time.time(),
                     last_event="connected to hub")

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
                        _publish_stats()  # keep the UI ticking when idle
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
                        _publish(last_event=f"handler error: "
                                           f"{type(exc).__name__}")
                        continue
                    if reply is not None:
                        await ws.send(json.dumps(
                            P.sign_message(reply, session.leg_key)))
                        _publish_stats()
            finally:
                hb.cancel()
        return 0

    try:
        rc = asyncio.run(_run())
    except KeyboardInterrupt:
        print("aali_node: stopped by user")
        _publish(state="stopped", last_event="stopped by user")
        return 0
    except Exception as exc:
        print(f"aali_node: connection failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        _publish(state="error", last_error=f"{type(exc).__name__}: {exc}")
        return 1
    _publish(state="stopped")
    return rc


def _run_update_check(update_hub: str, token: str, verification_key: str,
                      workspace_root: str | Path,
                      status: "dict[str, Any] | None" = None) -> None:
    """Best-effort signed update check before connecting. Never fatal."""

    def _publish(**fields: Any) -> None:
        if status is None:
            return
        with contextlib.suppress(Exception):
            status.update(fields)

    import aali_node
    from aali_node import updater
    try:
        client = updater.UpdateClient(
            update_hub, verification_key, workspace_root,
            current_version=aali_node.__version__)
        staged = client.check_and_stage(token)
        if staged is None:
            print("aali_node: update check: up to date "
                  f"({aali_node.__version__})")
            _publish(last_event=f"update check: up to date "
                                f"({aali_node.__version__})")
        else:
            print(f"aali_node: staged verified update → {staged} "
                  "(activate by restarting through the new install)")
            _publish(last_event=f"verified update staged: {staged.name}")
    except updater.NodeUpdateError as exc:
        # fail-closed refusal (bad signature / hash / zip) — logged, and
        # the Node keeps serving the version it already trusts
        print(f"aali_node: update check failed: {exc}", file=sys.stderr)
        _publish(last_event="update check failed (refused)")
    except Exception as exc:  # unexpected — same policy, louder
        print(f"aali_node: update check crashed: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        _publish(last_event="update check crashed")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles / redirected files default to cp1252: prints with
    # '→' CRASH (no cp1252 mapping) and '…' mojibakes. Force UTF-8 on the
    # CLI entrypoint (the smoke/mission_clock lesson; embedders calling
    # run_node() directly keep their own stdio policy).
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")
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
    parser.add_argument("--session-id",
                        default=os.getenv("AALI_NODE_SESSION", ""),
                        help="pin the session id (leg keys derive from "
                             "token+session; random when omitted — pin it "
                             "when a dispatcher needs a stable session)")
    parser.add_argument("--no-commands", action="store_true",
                        help="disable run_command entirely on this Node")
    parser.add_argument("--native-confirm", action="store_true",
                        help="ask via native OS dialogs instead of the "
                             "console (GUI-shell mode)")
    parser.add_argument("--update-hub", default=os.getenv("AALI_UPDATE_HUB"),
                        help="opt-in auto-update: Hub base URL, e.g. "
                             "http://hub.example:8080 (env AALI_UPDATE_HUB)")
    parser.add_argument("--update-key",
                        default=os.getenv("AALI_NODE_UPDATE_KEY"),
                        help="update-manifest verification key (HMAC "
                             "shared secret; env AALI_NODE_UPDATE_KEY)")
    parser.add_argument("--shell", action="store_true",
                        help="GUI mode: pywebview window + system tray "
                             "instead of the console (native confirmations "
                             "are always on in the shell)")
    parser.add_argument("--activate-update", action="store_true",
                        help="swap a previously STAGED update into this "
                             "install (staged under <install>/staged/ by "
                             "--update-hub), optionally restarting into "
                             "it; the Node does NOT serve in this mode")
    args = parser.parse_args(argv)

    if not args.token:
        print("aali_node: no token — log in via the Hub "
              "(POST /auth/login) and pass --token or set AALI_NODE_TOKEN",
              file=sys.stderr)
        return 2
    if len(args.token) > 4096:
        print("aali_node: token unreasonably long", file=sys.stderr)
        return 2

    if args.update_hub and not args.update_key:
        print("aali_node: --update-hub needs --update-key (or env "
              "AALI_NODE_UPDATE_KEY) — refusing to check updates it "
              "could not verify", file=sys.stderr)
        return 2

    install_root = _detect_install_root()

    if args.shell:
        from aali_node.shell import run_shell
        return run_shell(args.hub, args.token, args.workspace,
                         allow_commands=not args.no_commands,
                         update_hub=args.update_hub,
                         update_key=args.update_key,
                         install_root=install_root)

    if args.activate_update:
        from aali_node.activate import (spawn_detached_activation,
                                        staged_version)
        staged_dir = install_root / "staged"
        if not staged_dir.is_dir():
            print("aali_node: nothing staged under "
                  f"{staged_dir} — run with --update-hub first",
                  file=sys.stderr)
            return 2
        newest = max((d for d in staged_dir.iterdir() if d.is_dir()),
                     key=lambda d: d.name, default=None)
        if newest is None:
            print("aali_node: staged/ exists but holds no version",
                  file=sys.stderr)
            return 2
        try:
            staged_version(newest)
            print(f"aali_node: activating staged update {newest.name} …")
        except Exception as exc:
            print(f"aali_node: staged directory invalid: {exc}",
                  file=sys.stderr)
            return 2
        if not spawn_detached_activation(
                newest, install_root,
                restart_args=["--hub", args.hub, "--token", args.token,
                              "--workspace", args.workspace,
                              "--native-confirm"]
                + (["--update-hub", args.update_hub,
                    "--update-key", args.update_key]
                   if args.update_hub else [])):
            print("aali_node: could not launch the activation worker",
                  file=sys.stderr)
            return 1
        print("aali_node: activation worker launched — this Node exits; "
              "the log is activate_update.log next to the install")
        return 0

    return run_node(args.hub, args.token, args.workspace,
                    allow_commands=not args.no_commands,
                    session_id=args.session_id or None,
                    native_confirm=args.native_confirm,
                    update_hub=args.update_hub,
                    update_key=args.update_key,
                    install_root=install_root)


def _detect_install_root() -> "Path":
    """Where THIS Node is installed (updates stage/activate there).

    Frozen (PyInstaller) builds: the exe's directory. Source runs: the
    parent of the aali_node package (the deploy root), NOT the workspace —
    the workspace is user data and must never hold program files.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    import aali_node as _pkg
    return Path(_pkg.__file__).resolve().parents[1]


if __name__ == "__main__":
    sys.exit(main())
