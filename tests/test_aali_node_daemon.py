"""Tests for aali_node.daemon — session handling and Hub leg integration.

The integration test wires the REAL Hub Broker + ConnectionManager (pure
logic, no sockets) to a real NodeSession + sandbox: a brain-signed dispatch
must survive hub re-signing, node verification, execution, and hub relay
back to the brain leg — every hop signature-verified.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aali_node import daemon as ND
from aali_node import sandbox as SB
from file_agent import protocol as P
from aali_hub import auth as hub_auth
from aali_hub.ws_gateway import Broker, ConnectionInfo, ConnectionManager, \
    brain_leg_key

JWT_SECRET = "test-jwt-secret"
BRAIN_TOKEN = "test-brain-token"


def make_session(tmp_path: Path, *, session_id: str = "sess1",
                 token: str = "user-jwt-token",
                 confirm_hook=None) -> ND.NodeSession:
    box = SB.SecureSandbox(tmp_path / "ws", confirm_hook=confirm_hook)
    return ND.NodeSession(box, session_id, "node-test", token)


def test_constructor_refuses_empty_token(tmp_path: Path) -> None:
    box = SB.SecureSandbox(tmp_path / "ws")
    with pytest.raises(ValueError):
        ND.NodeSession(box, "s", "n", "")


def signed_call(tool: str, args: dict, key: bytes, **kw) -> dict:
    return P.sign_message(P.make_tool_call(tool, args, **kw), key)


# ---------------------------------------------------------------------------
# session-level (pure)
# ---------------------------------------------------------------------------
def test_drops_unsigned_message(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    msg = P.make_tool_call("list_files", {"path": "."})
    assert session.handle_message(msg) is None
    assert session.stats["dropped"] == 1


def test_drops_wrong_key_signature(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    forged = P.sign_message(P.make_tool_call("list_files", {"path": "."}),
                            b"not-the-leg-key")
    assert session.handle_message(forged) is None


def test_ping_gets_verified_pong(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    reply = session.handle_message(
        P.sign_message(P.make_ping(), session.leg_key))
    assert reply is not None and reply["type"] == P.TYPE_PONG
    # the transport signs the unsigned reply before it goes on the wire
    signed = P.sign_message(reply, session.leg_key)
    P.verify_message(signed, session.leg_key)  # must not raise
    assert session.stats["pongs_sent"] == 1


def test_tool_call_executes_and_echoes_id(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    call = signed_call("write_file",
                       {"path": "f.txt", "content": "hello"},
                       session.leg_key)
    reply = session.handle_message(call)
    assert reply is not None
    assert reply["type"] == P.TYPE_TOOL_RESULT
    assert reply["status"] == "ok"
    assert reply["id"] == call["id"]
    assert (session.sandbox.root / "f.txt").exists()
    assert session.stats["executed"] == 1


def test_denied_call_counts_as_denied(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    reply = session.handle_message(
        signed_call("delete_file", {"path": "x.txt"}, session.leg_key))
    assert reply is not None and reply["status"] == "denied"
    assert session.stats["denied"] == 1


def test_unknown_type_dropped(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    weird = P.sign_message({"type": "surprise", "v": P.PROTOCOL_VERSION,
                            "id": "x", "ts": 0.0}, session.leg_key)
    assert session.handle_message(weird) is None


def test_hello_payload_shape(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    hello = session.hello()
    assert hello["role"] == "node"
    assert hello["node_id"] == "node-test"
    assert hello["session_id"] == "sess1"
    assert "token" not in hello  # token is bound separately, not in hello()


# ---------------------------------------------------------------------------
# CLI guards (venv-independent)
# ---------------------------------------------------------------------------
def test_main_requires_token(capsys: pytest.CaptureFixture) -> None:
    assert ND.main(["--hub", "ws://x"]) == 2
    assert "no token" in capsys.readouterr().err


def test_main_rejects_huge_token() -> None:
    assert ND.main(["--token", "t" * 5000]) == 2


def test_run_node_without_websockets_returns_2(tmp_path: Path) -> None:
    if importlib.util.find_spec("websockets") is not None:
        pytest.skip("websockets installed — nothing to test here")
    rc = ND.run_node("ws://127.0.0.1:1", "tok", tmp_path / "ws")
    assert rc == 2


# ---------------------------------------------------------------------------
# end-to-end: brain → hub → node → hub → brain (real broker, no sockets)
# ---------------------------------------------------------------------------
def test_full_signed_roundtrip_through_real_broker(tmp_path: Path) -> None:
    manager = ConnectionManager()
    broker = Broker(manager, jwt_secret=JWT_SECRET, brain_token=BRAIN_TOKEN)

    jwt = hub_auth.mint_token("user-1", "user", JWT_SECRET, ttl_sec=3600)
    node_info = ConnectionInfo(conn_id="node1", role="node", user_id="user-1",
                               node_id="node-test", token=jwt,
                               session_id="sess1")
    brain_info = ConnectionInfo(conn_id="brain1", role="brain",
                                token=BRAIN_TOKEN)
    manager.register(node_info)
    manager.register(brain_info)

    session = make_session(tmp_path, session_id="sess1", token=jwt)

    # brain proposes a dispatch (signed with the brain leg)
    call = P.make_tool_call("write_file", {"path": "e2e.txt",
                                           "content": "e2e"},
                            requires_confirmation=False)
    call["session_id"] = "sess1"
    dispatch = P.sign_message(P.make_tool_dispatch("user-1", call),
                              brain_leg_key(BRAIN_TOKEN))
    actions = broker.handle_message("brain1", dispatch)
    assert len(actions) == 1 and actions[0].conn_id == "node1"

    # node verifies + executes + answers (transport signs with the node leg)
    reply = session.handle_message(actions[0].message)
    assert reply is not None and reply["status"] == "ok"
    relayed = broker.handle_message("node1",
                                    P.sign_message(reply, session.leg_key))
    assert len(relayed) == 1 and relayed[0].conn_id == "brain1"

    # brain leg verifies the relayed result
    final = relayed[0].message
    P.verify_message(final, brain_leg_key(BRAIN_TOKEN))
    assert final["type"] == P.TYPE_TOOL_RESULT
    assert final["id"] == call["id"]
    assert broker.stats["dispatched"] == 1
    assert broker.stats["relayed"] == 1


def test_broker_drops_node_result_for_unknown_call(tmp_path: Path) -> None:
    manager = ConnectionManager()
    broker = Broker(manager, jwt_secret=JWT_SECRET, brain_token=BRAIN_TOKEN)
    jwt = hub_auth.mint_token("user-1", "user", JWT_SECRET, ttl_sec=3600)
    manager.register(ConnectionInfo(conn_id="node1", role="node",
                                    user_id="user-1", node_id="n",
                                    token=jwt, session_id="sess1"))
    manager.register(ConnectionInfo(conn_id="brain1", role="brain",
                                    token=BRAIN_TOKEN))
    orphan = P.make_tool_result("no-such-call", "ok", {"x": 1})
    assert broker.handle_message("node1",
                                 P.sign_message(orphan,
                                                brain_leg_key("junk"))) == []
    # (wrong-leg signature: dropped before the pending-map is even consulted)


def test_broker_sends_nothing_when_node_offline(tmp_path: Path) -> None:
    manager = ConnectionManager()
    broker = Broker(manager, jwt_secret=JWT_SECRET, brain_token=BRAIN_TOKEN)
    manager.register(ConnectionInfo(conn_id="brain1", role="brain",
                                    token=BRAIN_TOKEN))
    call = P.make_tool_call("list_files", {"path": "."})
    call["session_id"] = "sess1"
    dispatch = P.sign_message(P.make_tool_dispatch("ghost-user", call),
                              brain_leg_key(BRAIN_TOKEN))
    assert broker.handle_message("brain1", dispatch) == []
    assert broker.stats["node_offline"] == 1


# ---------------------------------------------------------------------------
# ask loop: user_request (node→brain) + final_reply (brain→node)
# ---------------------------------------------------------------------------
def _register_node_and_brain(manager: ConnectionManager) -> str:
    jwt = hub_auth.mint_token("user-1", "user", JWT_SECRET, ttl_sec=3600)
    manager.register(ConnectionInfo(conn_id="node1", role="node",
                                    user_id="user-1", node_id="n",
                                    token=jwt, session_id="sess1"))
    manager.register(ConnectionInfo(conn_id="brain1", role="brain",
                                    token=BRAIN_TOKEN))
    return jwt


def test_broker_forwards_ask_with_verified_identity() -> None:
    manager = ConnectionManager()
    broker = Broker(manager, jwt_secret=JWT_SECRET, brain_token=BRAIN_TOKEN)
    jwt = _register_node_and_brain(manager)
    leg_key = P.derive_session_key(jwt, "sess1")
    ask = P.make_user_request("evil-user", "sess1", "مرحبا")  # spoof attempt
    actions = broker.handle_message("node1", P.sign_message(ask, leg_key))
    assert len(actions) == 1 and actions[0].conn_id == "brain1"
    forwarded = actions[0].message
    # the JWT-verified identity overrides whatever the node claimed
    assert forwarded["user_id"] == "user-1"
    P.verify_message(forwarded, brain_leg_key(BRAIN_TOKEN))
    assert broker.stats["asks_forwarded"] == 1


def test_broker_answers_node_when_brain_offline() -> None:
    manager = ConnectionManager()
    broker = Broker(manager, jwt_secret=JWT_SECRET, brain_token=BRAIN_TOKEN)
    jwt = hub_auth.mint_token("user-1", "user", JWT_SECRET, ttl_sec=3600)
    manager.register(ConnectionInfo(conn_id="node1", role="node",
                                    user_id="user-1", node_id="n",
                                    token=jwt, session_id="sess1"))
    leg_key = P.derive_session_key(jwt, "sess1")
    ask = P.make_user_request("user-1", "sess1", "hello?")
    actions = broker.handle_message("node1", P.sign_message(ask, leg_key))
    assert len(actions) == 1 and actions[0].conn_id == "node1"
    notice = actions[0].message
    P.verify_message(notice, leg_key)
    assert notice["type"] == P.TYPE_FINAL_REPLY
    assert "offline" in notice["reply"]
    assert broker.stats["brain_offline"] == 1


def test_broker_relays_final_reply_to_node() -> None:
    manager = ConnectionManager()
    broker = Broker(manager, jwt_secret=JWT_SECRET, brain_token=BRAIN_TOKEN)
    jwt = _register_node_and_brain(manager)  # same token = same leg key
    reply = P.make_final_reply("user-1", "Here is your answer.",
                               suggestions=["next?"])
    actions = broker.handle_message(
        "brain1", P.sign_message(reply, brain_leg_key(BRAIN_TOKEN)))
    assert len(actions) == 1 and actions[0].conn_id == "node1"
    P.verify_message(actions[0].message,
                     P.derive_session_key(jwt, "sess1"))
    assert actions[0].message["reply"] == "Here is your answer."
    assert broker.stats["replies_relayed"] == 1


def test_broker_drops_reply_for_offline_user() -> None:
    manager = ConnectionManager()
    broker = Broker(manager, jwt_secret=JWT_SECRET, brain_token=BRAIN_TOKEN)
    manager.register(ConnectionInfo(conn_id="brain1", role="brain",
                                    token=BRAIN_TOKEN))
    reply = P.make_final_reply("ghost", "...")
    assert broker.handle_message(
        "brain1", P.sign_message(reply, brain_leg_key(BRAIN_TOKEN))) == []
    assert broker.stats["node_offline"] == 1


def test_json_roundtrip_of_signed_messages(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    call = signed_call("list_files", {"path": "."}, session.leg_key)
    encoded = json.dumps(call)
    reply = session.handle_message(json.loads(encoded))
    assert reply is not None and reply["status"] == "ok"
