"""Aali Hub WebSocket gateway — connects Brains and Nodes to the Hub.

Transport auth (``hello`` handshake, NOT part of the signed protocol):

    Node  → {"type": "hello", "role": "node",  "token": <user access JWT>,
             "node_id": "<machine-chosen id>"}
    Brain → {"type": "hello", "role": "brain", "token": <AALI_BRAIN_TOKEN>}

Signing keys per leg (file_agent/protocol.py messages on the wire):

    brain↔hub leg : derived from the brain token itself
                    (derive_session_key(brain_token, "brain-leg")) — both
                    sides know the shared secret, no master key needed.
    hub↔node leg  : derived from the user's own JWT
                    (derive_session_key(jwt, session_id)) — the Hub learns
                    the JWT at hello time, the Node already holds it, so the
                    global protocol master key never leaves the Hub.

Every inbound message is signature-verified on its leg; every outbound
message is signed for the destination leg. The Hub is the only bridge
between legs — a Node can never forge brain traffic and vice versa.

The :class:`Broker` is pure logic returning :class:`Outbound` actions; the
FastAPI layer (``build_router``) runs real sockets with per-connection
asyncio send queues so cross-connection delivery works. FastAPI imports
lazily — the module loads (and is unit-testable) in any venv.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aali_hub import auth as hub_auth
from aali_hub import protocol as P

# FastAPI imports live at MODULE level on purpose: under PEP 563 ( postponed
# annotations) FastAPI resolves endpoint annotations via get_type_hints
# against module globals — a function-local ``WebSocket`` import is
# unresolvable there and the parameter silently degrades to a query param.
try:
    from fastapi import APIRouter, WebSocket, WebSocketDisconnect
    _HAS_FASTAPI = True
except ImportError:  # pure-logic/broker usage in any venv
    APIRouter = None  # type: ignore[assignment]
    WebSocket = None  # type: ignore[assignment]
    WebSocketDisconnect = Exception  # type: ignore[assignment,misc]
    _HAS_FASTAPI = False

__all__ = ["HelloError", "Outbound", "ConnectionInfo", "ConnectionManager",
           "Broker", "build_router"]

_HELLO_ROLES = ("node", "brain")
_BRAIN_LEG = "brain-leg"


def brain_leg_key(brain_token: str) -> bytes:
    """Derive the brain↔hub signing key from the shared brain token."""
    return P.derive_session_key(brain_token, _BRAIN_LEG)


class HelloError(Exception):
    """Handshake rejection (bad role, bad token, missing id)."""


@dataclass
class Outbound:
    """One signed message destined for one connection."""

    conn_id: str
    message: dict[str, Any]


@dataclass
class ConnectionInfo:
    """State attached to one live connection."""

    conn_id: str
    role: str                      # "node" | "brain"
    user_id: str = ""              # node only
    node_id: str = ""              # node only
    token: str = ""                # node JWT / brain token (leg key material)
    session_id: str = ""           # node only — binds the leg key
    alive: bool = True
    send: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def leg_key(self) -> bytes:
        """The signing key for THIS connection's leg."""
        if self.role == "brain":
            return brain_leg_key(self.token)
        return P.derive_session_key(self.token, self.session_id)


class ConnectionManager:
    """Tracks live connections; lookup by id / user / role."""

    def __init__(self) -> None:
        self._conns: dict[str, ConnectionInfo] = {}

    def register(self, info: ConnectionInfo) -> None:
        self._conns[info.conn_id] = info

    def unregister(self, conn_id: str) -> ConnectionInfo | None:
        return self._conns.pop(conn_id, None)

    def get(self, conn_id: str) -> ConnectionInfo | None:
        return self._conns.get(conn_id)

    def node_for_user(self, user_id: str) -> ConnectionInfo | None:
        """The user's most recently connected live node (if any)."""
        candidates = [c for c in self._conns.values()
                      if c.role == "node" and c.user_id == user_id and c.alive]
        return candidates[-1] if candidates else None

    def brain(self) -> ConnectionInfo | None:
        candidates = [c for c in self._conns.values()
                      if c.role == "brain" and c.alive]
        return candidates[0] if candidates else None

    def live_count(self, role: str | None = None) -> int:
        if role is None:
            return sum(1 for c in self._conns.values() if c.alive)
        return sum(1 for c in self._conns.values()
                   if c.role == role and c.alive)


class Broker:
    """Pure routing decisions for gateway messages (no sockets)."""

    def __init__(self, manager: ConnectionManager, *,
                 jwt_secret: str, brain_token: str,
                 new_conn_id: Callable[[], str] | None = None) -> None:
        self.manager = manager
        self._jwt_secret = jwt_secret
        self._brain_token = brain_token
        self._new_conn_id = new_conn_id or (lambda: secrets.token_hex(8))
        # tool_call id → proposing brain's conn id
        self._pending_results: dict[str, str] = {}
        self.stats = {"inbound_dropped": 0, "node_offline": 0,
                      "dispatched": 0, "relayed": 0}

    # ------------------------------------------------------------------
    # hello
    # ------------------------------------------------------------------
    def new_connection_id(self) -> str:
        return self._new_conn_id()

    def handle_hello(self, conn_id: str, payload: dict[str, Any]
                     ) -> ConnectionInfo:
        """Validate a hello; returns the registered ConnectionInfo."""
        if payload.get("type") != "hello":
            raise HelloError("first message must be a hello")
        role = payload.get("role")
        if role not in _HELLO_ROLES:
            raise HelloError(f"role must be one of {_HELLO_ROLES}")
        token = str(payload.get("token") or "")
        if role == "brain":
            if not hub_auth.check_brain_token(token, self._brain_token):
                raise HelloError("invalid brain token")
            info = ConnectionInfo(conn_id=conn_id, role="brain", token=token)
        else:
            node_id = str(payload.get("node_id") or "").strip()
            session_id = str(payload.get("session_id") or "").strip()
            if not node_id or not session_id:
                raise HelloError("node hello requires node_id and session_id")
            try:
                claims = hub_auth.verify_token(token, self._jwt_secret,
                                               kind="access")
            except hub_auth.AuthError as exc:
                raise HelloError(f"invalid node token: {exc.code}") from exc
            info = ConnectionInfo(conn_id=conn_id, role="node",
                                  user_id=str(claims["sub"]),
                                  node_id=node_id, token=token,
                                  session_id=session_id)
        self.manager.register(info)
        return info

    # ------------------------------------------------------------------
    # message routing
    # ------------------------------------------------------------------
    def handle_message(self, conn_id: str,
                       message: dict[str, Any]) -> list[Outbound]:
        """Verify + route one inbound message; returns signed outbound actions.

        Anything that fails verification or is malformed is dropped and
        counted — never relayed, never crash the connection.
        """
        info = self.manager.get(conn_id)
        if info is None or not info.alive:
            return []
        msg_type = message.get("type")

        # ---- heartbeat (leg-signed like everything else) ---------------
        if msg_type == P.TYPE_PING:
            try:
                P.parse_message(message, info.leg_key(), expect=P.TYPE_PING)
            except P.ProtocolError:
                self.stats["inbound_dropped"] += 1
                return []
            return [Outbound(conn_id,
                             P.sign_message(P.make_pong(message),
                                            info.leg_key()))]

        # ---- brain proposes a tool dispatch -----------------------------
        if info.role == "brain" and msg_type == P.TYPE_TOOL_DISPATCH:
            try:
                P.parse_message(message, brain_leg_key(info.token),
                                expect=P.TYPE_TOOL_DISPATCH)
            except P.ProtocolError:
                self.stats["inbound_dropped"] += 1
                return []
            user_id = str(message.get("user_id") or "")
            tool_call = message.get("tool_call")
            if not user_id or not isinstance(tool_call, dict) \
                    or not tool_call.get("id"):
                self.stats["inbound_dropped"] += 1
                return []
            node = self.manager.node_for_user(user_id)
            if node is None:
                self.stats["node_offline"] += 1
                return []
            # re-sign for the node leg with the tool_call's own session_id
            node_session = str(tool_call.get("session_id")
                               or node.session_id)
            node_key = P.derive_session_key(node.token, node_session)
            self._pending_results[str(tool_call["id"])] = conn_id
            self.stats["dispatched"] += 1
            return [Outbound(node.conn_id, P.sign_message(tool_call, node_key))]

        # ---- node returns a tool result ----------------------------------
        if info.role == "node" and msg_type == P.TYPE_TOOL_RESULT:
            try:
                P.parse_message(message, info.leg_key(),
                                expect=P.TYPE_TOOL_RESULT)
            except P.ProtocolError:
                self.stats["inbound_dropped"] += 1
                return []
            tool_call_id = str(message.get("id") or "")
            brain_conn = self._pending_results.pop(tool_call_id, None)
            if brain_conn is None:
                self.stats["inbound_dropped"] += 1
                return []
            brain = self.manager.get(brain_conn)
            if brain is None or not brain.alive:
                self.stats["inbound_dropped"] += 1
                return []
            self.stats["relayed"] += 1
            return [Outbound(brain_conn,
                             P.sign_message(message, brain.leg_key()))]

        self.stats["inbound_dropped"] += 1
        return []

    def node_offline(self, user_id: str) -> bool:
        """True when the user has no live node (brain told 'unavailable')."""
        return self.manager.node_for_user(user_id) is None


# ---------------------------------------------------------------------------
# FastAPI transport (lazy import)
# ---------------------------------------------------------------------------
def build_router(broker: Broker, manager: ConnectionManager):  # noqa: ANN201
    """Build the FastAPI WebSocket router (requires FastAPI)."""
    if not _HAS_FASTAPI:
        raise RuntimeError(
            "FastAPI is required to serve the Hub (pip install fastapi "
            "uvicorn[standard])")

    router = APIRouter()

    async def _serve(ws: WebSocket) -> None:
        conn_id = broker.new_connection_id()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        await ws.accept()

        hello = await ws.receive_json()
        try:
            info = broker.handle_hello(conn_id, hello)
        except HelloError:
            await ws.close(code=4401)
            return
        except Exception as exc:  # surface bugs instead of silent closes
            await ws.close(code=1011, reason=f"hello error: {exc}")
            return

        async def sender(message: dict[str, Any]) -> None:
            await queue.put(message)

        info.send = sender
        sender_task = asyncio.create_task(_drain(ws, queue))
        try:
            while True:
                try:
                    raw = await ws.receive_json()
                except WebSocketDisconnect:
                    break
                for action in broker.handle_message(conn_id, raw):
                    peer = manager.get(action.conn_id)
                    if peer is not None and peer.alive and peer.send:
                        await peer.send(action.message)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # surface bugs instead of silent closes
            await ws.close(code=1011, reason=f"handler error: {exc}")
        finally:
            info.alive = False
            manager.unregister(conn_id)
            await queue.put(None)
            sender_task.cancel()

    @router.websocket("/ws/brain")
    async def ws_brain(ws: WebSocket) -> None:
        await _serve(ws)

    @router.websocket("/ws/node")
    async def ws_node(ws: WebSocket) -> None:
        await _serve(ws)

    return router


async def _drain(ws: Any, queue: asyncio.Queue) -> None:
    """Send queued messages to this connection's socket until stopped."""
    while True:
        message = await queue.get()
        if message is None:
            return
        try:
            await ws.send_json(message)
        except Exception:  # socket died — stop draining
            return
