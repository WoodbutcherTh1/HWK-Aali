"""Tests for the aali_hub package — core logic (no HTTP).

Runs in ANY venv: FastAPI is not needed for these; argon2/pyjwt/cryptography
fall back to stdlib paths transparently.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "file-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

from aali_hub import audit as hub_audit  # noqa: E402
from aali_hub import auth as hub_auth  # noqa: E402
from aali_hub import model_registry  # noqa: E402
from aali_hub import update_server as us  # noqa: E402
from aali_hub import users_db, wol  # noqa: E402
from aali_hub.config import HubConfig, load_config  # noqa: E402
from aali_hub.protocol import ProtocolError, derive_session_key  # noqa: E402
from aali_hub.queue import Rejected, RequestQueue  # noqa: E402
from aali_hub.ws_gateway import (Broker, ConnectionInfo,  # noqa: E402
                                 ConnectionManager, HelloError)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def test_config_requires_jwt_secret_without_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AALI_HUB_JWT_SECRET", raising=False)
    monkeypatch.delenv("AALI_HUB_DEV", raising=False)
    with pytest.raises(RuntimeError):
        load_config({})


def test_config_dev_mode_generates_ephemeral_secrets() -> None:
    cfg = load_config({"AALI_HUB_DEV": "1"})
    assert cfg.jwt_secret and cfg.brain_token and cfg.master_key
    other = load_config({"AALI_HUB_DEV": "1"})
    assert other.jwt_secret != cfg.jwt_secret  # ephemeral, not shared


def test_config_env_overrides() -> None:
    cfg = load_config({"AALI_HUB_DEV": "1", "AALI_HUB_PORT": "9090",
                       "AALI_HUB_HOST": "0.0.0.0",
                       "AALI_BRAIN_URL": "http://10.0.0.5:5055/",
                       "AALI_DAILY_FREE": "7"})
    assert cfg.port == 9090 and cfg.host == "0.0.0.0"
    assert cfg.brain_url == "http://10.0.0.5:5055"  # trailing slash stripped
    assert cfg.daily_limit_free == 7


# ---------------------------------------------------------------------------
# users_db
# ---------------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path: Path) -> users_db.UsersDB:
    return users_db.UsersDB(tmp_path / "test.db")


def test_register_and_login_round_trip(db: users_db.UsersDB) -> None:
    user = db.create_user("User@Example.com", "password123")
    assert user["email"] == "user@example.com"
    assert "password_hash" not in user  # never leaks
    assert db.verify_login("user@example.com", "password123") is not None
    assert db.verify_login("user@example.com", "wrong") is None


def test_register_validation(db: users_db.UsersDB) -> None:
    with pytest.raises(users_db.UserError):
        db.create_user("not-an-email", "password123")
    with pytest.raises(users_db.UserError):
        db.create_user("a@b.co", "short")
    db.create_user("a@b.co", "password123")
    with pytest.raises(users_db.UserError):
        db.create_user("a@b.co", "password123")  # duplicate email


def test_roles_and_disable(db: users_db.UsersDB) -> None:
    user = db.create_user("a@b.co", "password123", role="pro")
    assert user["role"] == "pro_user"
    with pytest.raises(users_db.UserError):
        db.set_role(user["id"], "wizard")
    db.set_disabled(user["id"], True)
    assert db.verify_login("a@b.co", "password123") is None  # disabled


def test_api_key_lifecycle(db: users_db.UsersDB) -> None:
    user = db.create_user("a@b.co", "password123")
    key = db.create_api_key(user["id"], name="ci")
    assert key.startswith("aali-")
    resolved = db.resolve_api_key(key)
    assert resolved is not None and resolved["id"] == user["id"]
    assert db.resolve_api_key("aali-forged") is None
    # revoke via a prefix of the HASH (as surfaced by list_api_keys)
    listed = db.list_api_keys(user["id"])
    assert len(listed) == 1 and listed[0]["revoked"] in (0, False)
    assert db.revoke_api_key(listed[0]["key_hash"][:12], user["id"]) is True
    assert db.resolve_api_key(key) is None
    assert db.list_api_keys(user["id"])[0]["revoked"] in (1, True)


def test_usage_metering_accumulates(db: users_db.UsersDB) -> None:
    user = db.create_user("a@b.co", "password123")
    db.record_usage(user["id"], requests=1, tokens_in=10)
    db.record_usage(user["id"], requests=2, tokens_out=5, day="2026-09-13")
    today = db.usage_today(user["id"])
    assert today["requests"] == 1
    old = db.usage_for_day(user["id"], "2026-09-13")
    assert old["requests"] == 2 and old["tokens_out"] == 5


def test_audit_never_stores_raw_detail_unencoded(db: users_db.UsersDB) -> None:
    db.record_audit("u1", "admin_set_role", target="u2", detail="pro_user")
    events = db.query_audit(limit=10)
    assert events[0]["action"] == "admin_set_role"
    assert events[0]["detail"] == "pro_user"  # JSON-encoded, decoded on read


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
def test_jwt_round_trip() -> None:
    token = hub_auth.mint_token("u1", "admin", "secret", ttl_sec=60)
    claims = hub_auth.verify_token(token, "secret")
    assert claims["sub"] == "u1" and claims["role"] == "admin"
    assert claims["kind"] == "access"


def test_jwt_expiry_enforced() -> None:
    token = hub_auth.mint_token("u1", "admin", "secret", ttl_sec=60,
                                now=1_000_000)
    with pytest.raises(hub_auth.AuthError) as ei:
        hub_auth.verify_token(token, "secret", now=1_000_061)
    assert ei.value.code == "expired"


def test_jwt_wrong_secret_and_kind_rejected() -> None:
    token = hub_auth.mint_token("u1", "admin", "secret", ttl_sec=60)
    with pytest.raises(hub_auth.AuthError):
        hub_auth.verify_token(token, "other-secret")
    reset = hub_auth.mint_token("u1", "", "secret", ttl_sec=60, kind="reset")
    with pytest.raises(hub_auth.AuthError):
        hub_auth.verify_token(reset, "secret", kind="access")


def test_brain_token_constant_time_check() -> None:
    assert hub_auth.check_brain_token("tok", "tok") is True
    assert hub_auth.check_brain_token("tok", "other") is False
    assert hub_auth.check_brain_token(None, "tok") is False
    assert hub_auth.check_brain_token("tok", "") is False


# ---------------------------------------------------------------------------
# queue (fake clock — deterministic)
# ---------------------------------------------------------------------------
class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def qconfig() -> HubConfig:
    return HubConfig(dev_mode=True, jwt_secret="x", brain_token="y",
                     daily_limit_free=3, daily_limit_pro=100,
                     daily_limit_enterprise=1000,
                     user_requests_per_min=5, tool_calls_per_min=2,
                     max_concurrent_per_user=1, max_concurrent_tool_calls=1,
                     circuit_breaker_cooldown_sec=30.0)


def test_queue_daily_quota_by_tier(qconfig: HubConfig) -> None:
    clock = FakeClock()
    q = RequestQueue(qconfig, clock=clock)
    q.admit_request("u1", "free_user", used_today=2)
    with pytest.raises(Rejected) as ei:
        q.admit_request("u1", "free_user", used_today=3)
    assert ei.value.code == "daily_quota_exceeded"
    # pro tier has a higher ceiling
    q.admit_request("u2", "pro_user", used_today=99)
    with pytest.raises(Rejected):
        q.admit_request("u2", "pro_user", used_today=100)


def test_queue_per_minute_and_concurrency(qconfig: HubConfig) -> None:
    clock = FakeClock()
    q = RequestQueue(qconfig, clock=clock)
    for _ in range(5):
        q.admit_request("u1", "free_user", used_today=0)
        q.release_request("u1")  # each finishes before the next
    with pytest.raises(Rejected) as ei:
        q.admit_request("u1", "free_user", used_today=0)
    assert ei.value.code == "rate_limited"          # 6th inside the minute
    clock.advance(61)  # window slides
    q.admit_request("u1", "free_user", used_today=0)  # holds the only slot
    with pytest.raises(Rejected) as ei:
        q.admit_request("u1", "free_user", used_today=0)
    assert ei.value.code == "too_many_concurrent"
    q.release_request("u1")
    q.admit_request("u1", "free_user", used_today=0)


def test_queue_tool_call_limits_separate(qconfig: HubConfig) -> None:
    clock = FakeClock()
    q = RequestQueue(qconfig, clock=clock)
    q.admit_tool_call("u1", "free_user")
    with pytest.raises(Rejected):
        q.admit_tool_call("u1", "free_user")  # concurrent cap = 1
    q.release_tool_call("u1")
    q.admit_tool_call("u1", "free_user")   # 2nd of the minute: ok
    with pytest.raises(Rejected):
        q.admit_tool_call("u1", "free_user")  # 3rd: minute cap = 2


def test_queue_fair_round_robin(qconfig: HubConfig) -> None:
    clock = FakeClock()
    q = RequestQueue(qconfig, clock=clock)
    for user in ("a", "a", "b"):
        q.admit_request(user, "free_user", used_today=0)
        q.release_request(user)
        q.enqueue(user, "free_user")
    order = [q.pop_next().user_id for _ in range(3)]
    assert order == ["a", "b", "a"]  # never a,a,b


def test_queue_circuit_breaker(qconfig: HubConfig) -> None:
    clock = FakeClock()
    q = RequestQueue(qconfig, clock=clock)
    for _ in range(3):
        q.record_brain_failure()
    assert not q.brain_available()
    with pytest.raises(Rejected) as ei:
        q.admit_request("u1", "free_user", used_today=0)
    assert ei.value.code == "circuit_open"
    assert ei.value.retry_after is not None and ei.value.retry_after > 0
    clock.advance(31)
    assert q.brain_available()
    q.record_brain_success()
    assert q.brain_available()


# ---------------------------------------------------------------------------
# wol
# ---------------------------------------------------------------------------
def test_wol_magic_packet_shape() -> None:
    packet = wol.magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("aabbccddeeff")
    assert packet[96:102] == bytes.fromhex("aabbccddeeff")


def test_wol_mac_variants_and_errors() -> None:
    assert wol.parse_mac("aabbccddeeff") == wol.parse_mac(
        "AA-BB-CC-DD-EE-FF")
    with pytest.raises(wol.WolError):
        wol.parse_mac("nope")
    with pytest.raises(wol.WolError):
        wol.magic_packet("zz:bb:cc:dd:ee:ff")


# ---------------------------------------------------------------------------
# model_registry
# ---------------------------------------------------------------------------
@pytest.fixture()
def reg(tmp_path: Path) -> model_registry.ModelRegistry:
    return model_registry.ModelRegistry(tmp_path / "reg.db")


def test_registry_register_and_get(reg: model_registry.ModelRegistry) -> None:
    rec = reg.register("aali", "ckpt-3873", "/models/a", "safetensors",
                       context_length=4096, eval_score=3 / 26)
    assert rec["model_id"] == "aali" and rec["promoted"] is False
    with pytest.raises(model_registry.RegistryError):
        reg.register("aali", "ckpt-3873", "/models/a", "safetensors")
    with pytest.raises(model_registry.RegistryError):
        reg.register("aali", "v2", "/models/b", "onnx")  # bad format


def test_registry_promotion_is_exclusive(reg: model_registry.ModelRegistry) -> None:
    reg.register("aali", "v1", "/models/a", "pt")
    reg.register("aali", "v2", "/models/b", "pt")
    reg.mark_promoted("aali", "v1")
    assert reg.get("aali", "v1")["promoted"] is True
    reg.mark_promoted("aali", "v2")
    assert reg.get("aali", "v1")["promoted"] is False
    assert reg.get("aali", "v2")["promoted"] is True
    assert reg.promoted() == [reg.get("aali", "v2")]
    with pytest.raises(model_registry.RegistryError):
        reg.mark_promoted("aali", "v999")


def test_registry_update_flags(reg: model_registry.ModelRegistry) -> None:
    reg.register("aali", "v1", "/models/a", "pt")
    reg.update("aali", "v1", uploaded_to_hf=True, hf_repo="HWK/aali-110m",
               openrouter_listed=True)
    rec = reg.get("aali", "v1")
    assert rec["uploaded_to_hf"] is True and rec["hf_repo"] == "HWK/aali-110m"
    assert rec["openrouter_listed"] is True


# ---------------------------------------------------------------------------
# update_server
# ---------------------------------------------------------------------------
def test_update_manifest_sign_and_verify_hmac(tmp_path: Path) -> None:
    store = us.UpdateStore(tmp_path, "signing-secret", keep_versions=3)
    manifest = store.publish("1.0.1", "1.0.0", b"payload-bytes",
                             release_notes_ar="إصلاحات",
                             release_notes_en="fixes")
    assert manifest["sig_alg"] == "hmac-sha256"
    assert store.manifest_for("1.0.1") == manifest
    assert store.latest_manifest()["version"] == "1.0.1"
    assert store.artifact_bytes("1.0.1") == b"payload-bytes"
    # tamper → rejected
    tampered = dict(manifest, version="9.9.9")
    assert us.verify_manifest(tampered, "signing-secret") is False
    assert us.verify_manifest(manifest, "wrong-secret") is False


def test_update_retention_prunes_old_versions(tmp_path: Path) -> None:
    store = us.UpdateStore(tmp_path, "key", keep_versions=2)
    for v in ("1.0.0", "1.0.1", "1.0.2"):
        store.publish(v, "0.0.0", v.encode())
    assert store.list_versions() == ["1.0.2", "1.0.1"]  # 1.0.0 pruned
    with pytest.raises(us.UpdateError):
        store.manifest_for("1.0.0")


def test_update_bad_version_and_missing(tmp_path: Path) -> None:
    store = us.UpdateStore(tmp_path, "key")
    with pytest.raises(us.UpdateError):
        store.publish("../evil", "0", b"x")
    with pytest.raises(us.UpdateError):
        store.manifest_for("0.0.0")


# ---------------------------------------------------------------------------
# hub audit
# ---------------------------------------------------------------------------
def test_hub_audit_content_free_and_trimmed(tmp_path: Path) -> None:
    log = hub_audit.HubAudit(tmp_path / "audit.jsonl", max_entries=10)
    for i in range(15):
        log.log_tool_call("u1", "run_command", {"command": f"cmd-{i}"},
                          "ok", node_id="n1", duration_ms=10)
    events = log.query(limit=100)
    assert len(events) == 10                      # trimmed to newest
    assert events[0]["args_hash"] != events[-1]["args_hash"]
    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "cmd-" not in raw                      # raw args never stored
    assert "args_hash" in raw


# ---------------------------------------------------------------------------
# protocol shim
# ---------------------------------------------------------------------------
def test_hub_protocol_shim_matches_shared_module() -> None:
    from aali_hub import protocol as hub_proto
    from file_agent import protocol as shared
    assert hub_proto.PROTOCOL_VERSION == shared.PROTOCOL_VERSION
    assert hub_proto.make_tool_call is shared.make_tool_call
    key = derive_session_key("m", "s")
    msg = hub_proto.sign_message(hub_proto.make_ping(), key)
    hub_proto.parse_message(msg, key, expect=hub_proto.TYPE_PING)


# ---------------------------------------------------------------------------
# ws gateway broker
# ---------------------------------------------------------------------------
@pytest.fixture()
def gateway() -> tuple[Broker, ConnectionManager]:
    manager = ConnectionManager()
    broker = Broker(manager, jwt_secret="jwt-secret-0123456789abcdef",
                    brain_token="brain-token-0123456789abcdef",
                    new_conn_id=lambda: "conn-x")
    return broker, manager


def _node_hello(jwt: str | None = None) -> dict:
    from aali_hub import auth as hub_auth
    token = jwt or hub_auth.mint_token("u1", "free_user",
                                       "jwt-secret-0123456789abcdef",
                                       ttl_sec=60)
    return {"type": "hello", "role": "node", "token": token,
            "node_id": "node-1", "session_id": "sess-1"}


def _brain_hello() -> dict:
    return {"type": "hello", "role": "brain",
            "token": "brain-token-0123456789abcdef"}


def test_gateway_hello_auth(gateway: tuple[Broker, ConnectionManager]) -> None:
    broker, manager = gateway
    info = broker.handle_hello("n1", _node_hello())
    assert info.user_id == "u1" and info.role == "node"
    broker.handle_hello("b1", _brain_hello())
    assert manager.brain() is not None
    assert manager.node_for_user("u1") is not None


def test_gateway_hello_rejections(gateway: tuple[Broker, ConnectionManager]) -> None:
    broker, _ = gateway
    with pytest.raises(HelloError):
        broker.handle_hello("x", {"type": "hello", "role": "admin",
                                  "token": "x"})
    with pytest.raises(HelloError):
        broker.handle_hello("x", _brain_hello() | {"token": "wrong"})
    bad_node = _node_hello()
    bad_node["token"] = "forged"
    with pytest.raises(HelloError):
        broker.handle_hello("x", bad_node)


def test_gateway_dispatch_routes_to_node(gateway: tuple[Broker, ConnectionManager]) -> None:
    broker, manager = gateway
    node = broker.handle_hello("n1", _node_hello())
    broker.handle_hello("b1", _brain_hello())
    from file_agent import protocol as P
    call = P.make_tool_call("read_file", {"path": "notes.txt"})
    call["session_id"] = "sess-1"
    signed_dispatch = P.sign_message(
        P.make_tool_dispatch("u1", call),
        P.derive_session_key("brain-token-0123456789abcdef", "brain-leg"))
    actions = broker.handle_message("b1", signed_dispatch)
    assert len(actions) == 1
    assert actions[0].conn_id == "n1"
    # the outbound message is signed with the node's leg key and verifies
    node_key = P.derive_session_key(node.token, "sess-1")
    verified = P.parse_message(actions[0].message, node_key,
                               expect=P.TYPE_TOOL_CALL)
    assert verified["tool"] == "read_file"


def test_gateway_result_relays_to_brain(gateway: tuple[Broker, ConnectionManager]) -> None:
    from file_agent import protocol as P
    broker, manager = gateway
    node = broker.handle_hello("n1", _node_hello())
    broker.handle_hello("b1", _brain_hello())
    call = P.make_tool_call("read_file", {"path": "notes.txt"})
    call["session_id"] = "sess-1"
    signed_dispatch = P.sign_message(
        P.make_tool_dispatch("u1", call),
        P.derive_session_key("brain-token-0123456789abcdef", "brain-leg"))
    broker.handle_message("b1", signed_dispatch)
    result = P.make_tool_result(call["id"], "ok", {"text": "hello"},
                                duration_ms=12)
    node_key = P.derive_session_key(node.token, "sess-1")
    signed_result = P.sign_message(result, node_key)
    actions = broker.handle_message("n1", signed_result)
    assert len(actions) == 1 and actions[0].conn_id == "b1"
    brain_key = P.derive_session_key("brain-token-0123456789abcdef", "brain-leg")
    verified = P.parse_message(actions[0].message, brain_key,
                               expect=P.TYPE_TOOL_RESULT)
    assert verified["result"] == {"text": "hello"}
    assert broker.stats["relayed"] == 1


def test_gateway_forged_node_result_dropped(gateway: tuple[Broker, ConnectionManager]) -> None:
    from file_agent import protocol as P
    broker, _ = gateway
    broker.handle_hello("n1", _node_hello())
    broker.handle_hello("b1", _brain_hello())
    call = P.make_tool_call("read_file", {"path": "x"})
    call["session_id"] = "sess-1"
    signed_dispatch = P.sign_message(
        P.make_tool_dispatch("u1", call),
        P.derive_session_key("brain-token-0123456789abcdef", "brain-leg"))
    broker.handle_message("b1", signed_dispatch)
    forged = P.sign_message(
        P.make_tool_result(call["id"], "ok", {}),
        P.derive_session_key("jwt-secret", "sess-1"))  # WRONG leg key
    assert broker.handle_message("n1", forged) == []


def test_gateway_node_offline_dispatch_dropped(gateway: tuple[Broker, ConnectionManager]) -> None:
    from file_agent import protocol as P
    broker, _ = gateway
    broker.handle_hello("b1", _brain_hello())  # no node connected
    call = P.make_tool_call("read_file", {"path": "x"})
    signed_dispatch = P.sign_message(
        P.make_tool_dispatch("u1", call),
        P.derive_session_key("brain-token-0123456789abcdef", "brain-leg"))
    assert broker.handle_message("b1", signed_dispatch) == []
    assert broker.stats["node_offline"] == 1


def test_gateway_ping_pong_leg_signed(gateway: tuple[Broker, ConnectionManager]) -> None:
    from file_agent import protocol as P
    broker, _ = gateway
    node = broker.handle_hello("n1", _node_hello())
    node_key = P.derive_session_key(node.token, "sess-1")
    ping = P.sign_message(P.make_ping(), node_key)
    actions = broker.handle_message("n1", ping)
    assert len(actions) == 1
    assert P.parse_message(actions[0].message, node_key,
                           expect=P.TYPE_PONG)
