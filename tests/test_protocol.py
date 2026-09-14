"""Tests for file_agent/protocol.py (SaaS STEP 2)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent import protocol as P  # noqa: E402


# ---------------------------------------------------------------------------
# keys / HKDF
# ---------------------------------------------------------------------------
def test_session_key_derivation_is_deterministic() -> None:
    k1 = P.derive_session_key("master", "sess-1")
    k2 = P.derive_session_key("master", "sess-1")
    assert k1 == k2 and len(k1) == 32


def test_different_sessions_never_share_keys() -> None:
    assert P.derive_session_key("master", "sess-1") != \
        P.derive_session_key("master", "sess-2")


def test_empty_inputs_rejected() -> None:
    with pytest.raises(P.ProtocolError):
        P.derive_session_key("", "sess-1")
    with pytest.raises(P.ProtocolError):
        P.derive_session_key("master", "")


def test_get_master_key_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AALI_PROTOCOL_MASTER_KEY", raising=False)
    with pytest.raises(P.ProtocolError):
        P.get_master_key()
    monkeypatch.setenv("AALI_PROTOCOL_MASTER_KEY", "topsecret")
    assert P.get_master_key() == "topsecret"


# ---------------------------------------------------------------------------
# signing round-trip
# ---------------------------------------------------------------------------
def _key() -> bytes:
    return P.derive_session_key("master", "sess-1")


def test_sign_and_verify_round_trip() -> None:
    msg = P.make_tool_call("read_file", {"path": "a.txt"})
    signed = P.sign_message(msg, _key())
    assert "sig" in signed
    out = P.parse_message(signed, _key(), expect=P.TYPE_TOOL_CALL)
    assert out["tool"] == "read_file"


def test_tampered_payload_fails_signature() -> None:
    signed = P.sign_message(P.make_tool_call("read_file", {"path": "a.txt"}),
                            _key())
    signed["args"]["path"] = "../../../etc/passwd"
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(signed, _key())
    assert ei.value.code == "bad_signature"


def test_forged_signature_fails() -> None:
    signed = P.sign_message(P.make_tool_call("read_file", {"path": "a.txt"}),
                            _key())
    signed["sig"] = "0" * 64
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(signed, _key())
    assert ei.value.code == "bad_signature"


def test_wrong_key_fails() -> None:
    signed = P.sign_message(P.make_tool_call("read_file", {"path": "a.txt"}),
                            _key())
    other = P.derive_session_key("master", "sess-2")
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(signed, other)
    assert ei.value.code == "bad_signature"


# ---------------------------------------------------------------------------
# builders + field validation
# ---------------------------------------------------------------------------
def test_tool_call_carries_required_fields() -> None:
    msg = P.make_tool_call("run_command",
                           {"command": "pytest -q"}, requires_confirmation=True,
                           timeout_sec=30)
    assert msg["v"] == P.PROTOCOL_VERSION
    assert msg["type"] == "tool_call"
    assert msg["requires_confirmation"] is True
    assert msg["timeout_sec"] == 30
    assert msg["args"] == {"command": "pytest -q"}


def test_tool_result_status_whitelist() -> None:
    ok = P.make_tool_result("id-1", "ok", {"out": "hi"}, duration_ms=12)
    assert ok["status"] == "ok" and ok["duration_ms"] == 12
    for bad in ("maybe", "SUCCESS", ""):
        with pytest.raises(P.ProtocolError):
            P.make_tool_result("id-1", bad, {})


def test_tool_result_wraps_non_dict_result() -> None:
    msg = P.make_tool_result("id-1", "error", "boom")
    assert msg["result"] == {"result": "boom"}


def test_final_reply_includes_suggestions() -> None:
    msg = P.make_final_reply("u1", "hello", suggestions=["a", "b"])
    assert msg["suggestions"] == ["a", "b"]


def test_pong_echoes_ping_id() -> None:
    ping = P.make_ping()
    pong = P.make_pong(ping)
    assert pong["id"] == ping["id"]


# ---------------------------------------------------------------------------
# parse validation
# ---------------------------------------------------------------------------
def test_invalid_json_is_malformed() -> None:
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message("not json {", _key())
    assert ei.value.code == "malformed"


def test_unknown_type_rejected() -> None:
    msg = P.sign_message({"v": P.PROTOCOL_VERSION, "type": "sneaky",
                          "id": "x", "ts": time.time()}, _key())
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(msg, _key())
    assert ei.value.code == "unknown_type"


def test_missing_required_field_rejected() -> None:
    msg = P.make_user_request("u1", "s1", "hi")
    del msg["message"]
    signed = P.sign_message(msg, _key())
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(signed, _key())
    assert ei.value.code == "malformed"


def test_major_version_mismatch_rejected() -> None:
    msg = P.make_ping()
    msg["v"] = "2.0.0"
    signed = P.sign_message(msg, _key())
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(signed, _key())
    assert ei.value.code == "unsupported_version"


def test_minor_version_accepted() -> None:
    msg = P.make_ping()
    msg["v"] = "1.1.0"  # backward-compatible minor bump
    signed = P.sign_message(msg, _key())
    out = P.parse_message(signed, _key())
    assert out["v"] == "1.1.0"


def test_type_mismatch_detected() -> None:
    signed = P.sign_message(P.make_ping(), _key())
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(signed, _key(), expect=P.TYPE_PONG)
    assert ei.value.code == "type_mismatch"


def test_stale_message_rejected_replay_protection() -> None:
    msg = P.make_tool_call("read_file", {"path": "a.txt"})
    msg["ts"] = time.time() - (P.DEFAULT_MAX_AGE_SEC + 30)
    signed = P.sign_message(msg, _key())
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(signed, _key())
    assert ei.value.code == "stale_message"


def test_future_timestamp_beyond_skew_rejected() -> None:
    msg = P.make_ping()
    msg["ts"] = time.time() + P.MAX_CLOCK_SKEW_SEC + 120
    signed = P.sign_message(msg, _key())
    with pytest.raises(P.ProtocolError) as ei:
        P.parse_message(signed, _key())
    assert ei.value.code == "stale_message"


def test_json_bytes_round_trip() -> None:
    signed = P.sign_message(P.make_user_request("u1", "s1", "مرحبا يا آلي"),
                            _key())
    wire = json.dumps(signed, ensure_ascii=False).encode("utf-8")
    out = P.parse_message(wire, _key(), expect=P.TYPE_USER_REQUEST)
    assert out["message"] == "مرحبا يا آلي"


# ---------------------------------------------------------------------------
# confirmation reconciliation + backoff
# ---------------------------------------------------------------------------
def test_confirmation_reconciliation_is_safety_first() -> None:
    assert P.reconcile_confirmation(True, False) is True
    assert P.reconcile_confirmation(False, True) is True
    assert P.reconcile_confirmation(False, False) is False


def test_backoff_is_exponential_and_capped() -> None:
    d0 = P.backoff_delay(0, jitter=False)
    d3 = P.backoff_delay(3, jitter=False)
    assert d0 == 1.0
    assert d3 == 8.0
    assert P.backoff_delay(20, jitter=False) == 60.0  # cap
    lo, hi = P.backoff_delay(1), P.backoff_delay(1)
    assert 2.0 <= lo <= 2.0 * 1.3 and 2.0 <= hi <= 2.0 * 1.3
