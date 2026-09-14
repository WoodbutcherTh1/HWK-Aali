"""Versioned, signed JSON message protocol for Aali Cloud (Hub ↔ Node ↔ Brain).

SaaS mode transport contract (docs/SAAS_ARCHITECTURE.md STEP 2). Every message
is a JSON object with:

    v    — protocol version string "MAJOR.MINOR.PATCH"
           (MINOR bump = backward compatible, MAJOR bump = breaking)
    type — one of the TYPE_* constants below
    id   — uuid4 string unique per message
    ts   — sender unix timestamp (float seconds)
    sig  — HMAC-SHA256 hex digest over the canonical JSON of the message
           with the ``sig`` field removed

All other fields are type-specific. Signing keys are derived per session from
a master secret via HKDF-SHA256 (stdlib-only: hmac + hashlib).

Tool dispatch is always a *proposal*: a ``tool_call`` tells the Node what the
brain wants to run; the Node re-derives confirmation requirements from its own
tool table and denies anything unexpected. Replay protection: receivers reject
messages older than ``DEFAULT_MAX_AGE_SEC`` or from the future beyond
``MAX_CLOCK_SKEW_SEC``.

Pure stdlib — no third-party dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import time
import uuid
from typing import Any

__all__ = [
    "ProtocolError",
    "PROTOCOL_VERSION",
    "HEARTBEAT_INTERVAL_SEC",
    "DEFAULT_TOOL_TIMEOUT_SEC",
    "DEFAULT_MAX_AGE_SEC",
    "MAX_CLOCK_SKEW_SEC",
    "TYPE_TOOL_CALL",
    "TYPE_TOOL_RESULT",
    "TYPE_USER_REQUEST",
    "TYPE_TOOL_DISPATCH",
    "TYPE_FINAL_REPLY",
    "TYPE_PING",
    "TYPE_PONG",
    "KNOWN_TYPES",
    "derive_session_key",
    "get_master_key",
    "sign_message",
    "verify_message",
    "make_tool_call",
    "make_tool_result",
    "make_user_request",
    "make_tool_dispatch",
    "make_final_reply",
    "make_ping",
    "make_pong",
    "parse_message",
    "reconcile_confirmation",
    "version_major",
    "backoff_delay",
]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "1.0.0"
HEARTBEAT_INTERVAL_SEC = 30.0
DEFAULT_TOOL_TIMEOUT_SEC = 60
# Replay window: how long a received tool_call/user_request is considered
# fresh. Generous enough for slow transports, short enough to kill replays.
DEFAULT_MAX_AGE_SEC = 300.0
MAX_CLOCK_SKEW_SEC = 60.0

TYPE_TOOL_CALL = "tool_call"          # Hub→Node: proposal to run a tool
TYPE_TOOL_RESULT = "tool_result"      # Node→Hub: ok|error|denied|timeout
TYPE_USER_REQUEST = "user_request"    # Hub→Brain: chat turn for the brain
TYPE_TOOL_DISPATCH = "tool_dispatch"  # Brain→Hub: proposal to route to a Node
TYPE_FINAL_REPLY = "final_reply"      # Brain→Hub: finished answer + suggestions
TYPE_PING = "ping"                    # heartbeat both directions
TYPE_PONG = "pong"

KNOWN_TYPES = frozenset({
    TYPE_TOOL_CALL, TYPE_TOOL_RESULT, TYPE_USER_REQUEST,
    TYPE_TOOL_DISPATCH, TYPE_FINAL_REPLY, TYPE_PING, TYPE_PONG,
})

# Per-type required fields (beyond v/type/id/ts/sig).
_REQUIRED: dict[str, tuple[str, ...]] = {
    TYPE_TOOL_CALL: ("tool", "args", "requires_confirmation", "timeout_sec"),
    TYPE_TOOL_RESULT: ("status", "result", "duration_ms"),
    TYPE_USER_REQUEST: ("user_id", "session_id", "message"),
    TYPE_TOOL_DISPATCH: ("user_id", "tool_call"),
    TYPE_FINAL_REPLY: ("user_id", "reply"),
    TYPE_PING: (),
    TYPE_PONG: (),
}

_RESULT_STATUSES = frozenset({"ok", "error", "denied", "timeout"})


class ProtocolError(Exception):
    """Protocol violation with a machine-readable ``code``."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------

def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF with SHA-256 (extract-then-expand), stdlib-only."""
    if not salt:
        salt = b"\x00" * hashlib.sha256().digest_size
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]),
                         hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_session_key(master_key: str | bytes, session_id: str) -> bytes:
    """Derive a 32-byte per-session signing key from the master secret.

    Same (master, session) pair always yields the same key; different
    sessions never share keys, so a leaked session key cannot sign
    messages for another session.
    """
    if isinstance(master_key, str):
        master_key = master_key.encode("utf-8")
    if not master_key:
        raise ProtocolError("config_error", "master key must be non-empty")
    if not session_id:
        raise ProtocolError("config_error", "session_id must be non-empty")
    return _hkdf_sha256(
        ikm=master_key,
        salt=b"aali-cloud-v1",
        info=f"session:{session_id}".encode("utf-8"),
        length=32,
    )


def get_master_key(env: dict[str, str] | None = None) -> str:
    """Read the protocol master key from the environment.

    Uses ``AALI_PROTOCOL_MASTER_KEY``. Never logs or returns a default —
    a missing key is a hard configuration error.
    """
    source = os.environ if env is None else env
    value = source.get("AALI_PROTOCOL_MASTER_KEY", "")
    if not value:
        raise ProtocolError(
            "config_error",
            "AALI_PROTOCOL_MASTER_KEY is not set — refusing unsigned traffic",
        )
    return value


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------

def _canonical(msg: dict[str, Any]) -> bytes:
    payload = {k: v for k, v in msg.items() if k != "sig"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def sign_message(msg: dict[str, Any], key: bytes) -> dict[str, Any]:
    """Return a signed copy of *msg* (adds the ``sig`` field)."""
    digest = hmac.new(key, _canonical(msg), hashlib.sha256).hexdigest()
    signed = dict(msg)
    signed["sig"] = digest
    return signed


def verify_message(msg: dict[str, Any], key: bytes) -> None:
    """Verify the signature of *msg* in place; raise ProtocolError on failure."""
    if not isinstance(msg, dict):
        raise ProtocolError("malformed", "message must be a JSON object")
    sig = msg.get("sig")
    if not isinstance(sig, str) or not sig:
        raise ProtocolError("malformed", "missing signature")
    expected = hmac.new(key, _canonical(msg), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ProtocolError("bad_signature", "HMAC mismatch")


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def _base(msg_type: str) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": time.time(),
    }


def _check_fields(msg: dict[str, Any]) -> None:
    msg_type = msg.get("type")
    if msg_type not in KNOWN_TYPES:
        raise ProtocolError("unknown_type", f"type={msg_type!r}")
    for field in _REQUIRED[msg_type]:
        if field not in msg:
            raise ProtocolError("malformed",
                                f"{msg_type} missing field {field!r}")
    if msg_type == TYPE_TOOL_RESULT and msg["status"] not in _RESULT_STATUSES:
        raise ProtocolError("malformed",
                            f"status must be one of {sorted(_RESULT_STATUSES)}")


def make_tool_call(tool: str, args: dict[str, Any], *,
                   requires_confirmation: bool = False,
                   timeout_sec: int = DEFAULT_TOOL_TIMEOUT_SEC) -> dict[str, Any]:
    """Build an unsigned Hub→Node tool_call proposal."""
    if not isinstance(tool, str) or not tool:
        raise ProtocolError("malformed", "tool must be a non-empty string")
    if not isinstance(args, dict):
        raise ProtocolError("malformed", "args must be an object")
    msg = _base(TYPE_TOOL_CALL)
    msg.update({
        "tool": tool,
        "args": args,
        "requires_confirmation": bool(requires_confirmation),
        "timeout_sec": int(timeout_sec),
    })
    return msg


def make_tool_result(tool_call_id: str, status: str, result: dict[str, Any],
                     *, duration_ms: int = 0) -> dict[str, Any]:
    """Build an unsigned Node→Hub tool_result (status: ok|error|denied|timeout)."""
    if status not in _RESULT_STATUSES:
        raise ProtocolError("malformed",
                            f"status must be one of {sorted(_RESULT_STATUSES)}")
    msg = _base(TYPE_TOOL_RESULT)
    msg.update({
        "id": tool_call_id,  # a result echoes the tool_call id
        "status": status,
        "result": result if isinstance(result, dict) else {"result": result},
        "duration_ms": int(duration_ms),
    })
    return msg


def make_user_request(user_id: str, session_id: str, message: str,
                      attachments: list[str] | None = None) -> dict[str, Any]:
    """Build an unsigned Hub→Brain chat request."""
    msg = _base(TYPE_USER_REQUEST)
    msg.update({
        "user_id": user_id,
        "session_id": session_id,
        "message": message,
        "attachments": list(attachments or []),
    })
    return msg


def make_tool_dispatch(user_id: str, tool_call: dict[str, Any]) -> dict[str, Any]:
    """Build an unsigned Brain→Hub dispatch wrapping a signed tool_call."""
    msg = _base(TYPE_TOOL_DISPATCH)
    msg.update({"user_id": user_id, "tool_call": tool_call})
    return msg


def make_final_reply(user_id: str, reply: str,
                     suggestions: list[str] | None = None) -> dict[str, Any]:
    """Build an unsigned Brain→Hub final reply (with follow-up chips)."""
    msg = _base(TYPE_FINAL_REPLY)
    msg.update({
        "user_id": user_id,
        "reply": reply,
        "suggestions": list(suggestions or []),
    })
    return msg


def make_ping() -> dict[str, Any]:
    """Build an unsigned heartbeat ping."""
    return _base(TYPE_PING)


def make_pong(ping: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build an unsigned heartbeat pong (echoes the ping id when given)."""
    msg = _base(TYPE_PONG)
    if ping and isinstance(ping, dict) and "id" in ping:
        msg["id"] = ping["id"]
    return msg


# ---------------------------------------------------------------------------
# parsing / validation
# ---------------------------------------------------------------------------

def version_major(version: str) -> int:
    """Return the MAJOR component of a protocol version string."""
    try:
        return int(str(version).split(".")[0])
    except (ValueError, IndexError) as exc:
        raise ProtocolError("unsupported_version",
                            f"bad version {version!r}") from exc


def parse_message(raw: str | bytes | bytearray | dict[str, Any], key: bytes, *,
                  expect: str | None = None,
                  max_age_sec: float = DEFAULT_MAX_AGE_SEC,
                  now: float | None = None) -> dict[str, Any]:
    """Parse, version-check, authenticate and freshness-check one message.

    Returns the verified message dict. Raises :class:`ProtocolError` with a
    ``code`` of malformed | unknown_type | unsupported_version |
    bad_signature | stale_message | type_mismatch.
    """
    if isinstance(raw, str):
        try:
            msg = json.loads(raw)
        except ValueError as exc:
            raise ProtocolError("malformed", "invalid JSON") from exc
    elif isinstance(raw, (bytes, bytearray)):
        try:
            msg = json.loads(bytes(raw).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError("malformed", "invalid JSON") from exc
    else:
        msg = raw
    if not isinstance(msg, dict):
        raise ProtocolError("malformed", "message must be a JSON object")

    version = msg.get("v")
    if not isinstance(version, str):
        raise ProtocolError("malformed", "missing protocol version 'v'")
    if version_major(version) != version_major(PROTOCOL_VERSION):
        raise ProtocolError(
            "unsupported_version",
            f"peer major {version_major(version)} != ours "
            f"{version_major(PROTOCOL_VERSION)} (breaking change)",
        )

    _check_fields(msg)

    verify_message(msg, key)

    if max_age_sec is not None and "ts" in msg:
        current = time.time() if now is None else now
        age = current - float(msg["ts"])
        if age > max_age_sec:
            raise ProtocolError("stale_message",
                                f"message age {age:.1f}s > {max_age_sec:.1f}s")
        if age < -MAX_CLOCK_SKEW_SEC:
            raise ProtocolError("stale_message",
                                f"timestamp {age:.1f}s in the future")

    if expect is not None and msg["type"] != expect:
        raise ProtocolError("type_mismatch",
                            f"expected {expect!r}, got {msg['type']!r}")
    return msg


def reconcile_confirmation(msg_flag: bool, local_flag: bool) -> bool:
    """Combine the Hub's confirmation claim with the Node's own table.

    Safety-first: confirmation is required when EITHER side demands it.
    The Node must re-derive ``requires_confirmation`` from its own tool
    table and never trust a hub that claims a dangerous tool is safe.
    """
    return bool(msg_flag) or bool(local_flag)


def backoff_delay(attempt: int, *, base: float = 1.0, cap: float = 60.0,
                  jitter: bool = True) -> float:
    """Exponential reconnect backoff with jitter (capped at *cap* seconds).

    ``attempt`` is 0-based: attempt 0 waits ~1s, attempt 1 ~2s, … capped at
    60s per the architecture doc.
    """
    if attempt < 0:
        attempt = 0
    delay = min(cap, base * (2 ** attempt))
    if jitter:
        delay += random.uniform(0.0, 0.3 * delay)
    return delay
