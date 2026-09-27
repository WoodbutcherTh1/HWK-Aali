"""Tests for the Pi sentinel catcher (scripts/pi_sentinel_catcher.py).

The catcher is the Pi-side half of the WoL sentinel design: it proxies
Cloudflare-edge traffic to the home PC over LAN and wakes the PC with a
magic packet when it is asleep. These tests run the REAL catcher server
in-process against a REAL local upstream server - no network beyond
127.0.0.1, and wake_fn is always a recorder, so no real wake packets are
ever emitted from the suite.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import pi_sentinel_catcher as catcher
from pi_sentinel_catcher import (
    DEFAULT_MAX_BYTES,
    CatcherHandler,
    SentinelServer,
    build_magic_packet,
)


# ----------------------------------------------------------------- helpers

class UpstreamHandler(BaseHTTPRequestHandler):
    """Stand-in for the home PC's Aali API (only the shapes we need)."""

    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # silence
        pass

    def _capture(self, body: bytes) -> None:
        self.server.captured.append(  # type: ignore[attr-defined]
            (self.command, self.path, body))

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._capture(b"")
        if self.path == "/api/health":
            self._json(200, {"ok": True, "upstream": True, "mode": "local"})
        elif self.path == "/api/fail":
            self._json(500, {"ok": False, "error": "boom"})
        elif self.path == "/big":
            data = b"x" * 1000
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json(200, {"path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._capture(body)
        if self.path == "/api/ask/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(3):
                self.wfile.write(f"event: tick\ndata: chunk{i}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.02)
            return
        if self.path == "/api/fail":
            self._json(500, {"ok": False, "error": "boom"})
            return
        self._json(200, {"echo": body.decode("utf-8", "replace"),
                         "ctype": self.headers.get("Content-Type")})


@pytest.fixture()
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    server.captured = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def make_catcher(upstream_port: int | None, tmp_path, wakes: list):
    """Build a catcher whose wake_fn records instead of sending packets."""
    if upstream_port is None:
        # a port with no listener on loopback -> instant refusal
        probe = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        dead_port = probe.server_address[1]
        probe.server_close()
        upstream_port = dead_port

    def wake_fn(ip, mac, wake_port=9, broadcast=None, retries=3):
        wakes.append({"ip": ip, "mac": mac, "broadcast": broadcast})
        return True

    server = SentinelServer(
        ("127.0.0.1", 0), CatcherHandler,
        pc_ip="127.0.0.1", pc_port=upstream_port,
        pc_mac="F4:B5:20:46:44:27",
        log_file=str(tmp_path / "catcher.log"),
        max_bytes=DEFAULT_MAX_BYTES,
        wake_fn=wake_fn,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture()
def catcher_pair(upstream, tmp_path):
    """(catcher server, upstream, wakes) - named catcher_pair so the module
    alias `catcher` stays importable in tests."""
    wakes: list = []
    server = make_catcher(upstream.server_address[1], tmp_path, wakes)
    yield server, upstream, wakes
    server.shutdown()
    server.server_close()


def get(base: int, path: str, headers: dict | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:{base}{path}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read(), dict(err.headers)


def post(base: int, path: str, body: bytes = b"{}", headers: dict | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:{base}{path}",
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


# ------------------------------------------------------------------ tests

def test_magic_packet_layout():
    packet = build_magic_packet("F4:B5:20:46:44:27")
    assert packet == b"\xff" * 6 + bytes.fromhex("F4B520464427") * 16
    assert len(packet) == 102  # 6 + 16*6, the WoL standard frame


def test_magic_packet_accepts_bare_and_dashed_forms():
    assert build_magic_packet("F4B520464427") == build_magic_packet(
        "f4-b5-20-46-44-27")


def test_magic_packet_rejects_garbage():
    with pytest.raises(ValueError):
        build_magic_packet("not-a-mac")
    with pytest.raises(ValueError):
        build_magic_packet("F4:B5:20:46:44")  # 5 octets


def test_get_passes_through_verbatim(catcher_pair):
    server, upstream, _ = catcher_pair
    port = server.server_address[1]
    status, body, _ = get(port, "/api/some/path?sid=abc&keep=1")
    assert status == 200
    assert json.loads(body)["path"] == "/api/some/path?sid=abc&keep=1"
    assert upstream.captured[0][0] == "GET"


def test_post_body_and_content_type_survive(catcher_pair):
    server, upstream, _ = catcher_pair
    port = server.server_address[1]
    payload = json.dumps({"message": "مرحبا آلي", "sid": "s1"}).encode("utf-8")
    status, body = post(port, "/api/ask", payload,
                        headers={"X-API-Key": "k-test"})
    assert status == 200
    echoed = json.loads(body)
    assert echoed["echo"] == payload.decode("utf-8")
    assert echoed["ctype"].startswith("application/json")
    method, path, got_body = upstream.captured[0]
    assert (method, path) == ("POST", "/api/ask")
    assert got_body == payload


def test_upstream_error_status_forwarded_untouched(catcher_pair):
    server, _, _ = catcher_pair
    port = server.server_address[1]
    status, body, _ = get(port, "/api/fail")
    assert status == 500
    assert json.loads(body)["error"] == "boom"


def test_stream_is_forwarded_without_buffering(catcher_pair):
    server, _, _ = catcher_pair
    port = server.server_address[1]
    status, body = post(port, "/api/ask/stream", b"{}")
    assert status == 200
    text = body.decode()
    assert "chunk0" in text and "chunk1" in text and "chunk2" in text


def test_upstream_5xx_does_not_trigger_wake(catcher_pair):
    server, _, wakes = catcher_pair
    port = server.server_address[1]
    status, _, _ = get(port, "/api/fail")
    assert status == 500
    assert wakes == []  # a reachable-but-erroring PC is not asleep


def test_unreachable_pc_yields_502_plus_single_wake(tmp_path):
    wakes: list = []
    server = make_catcher(None, tmp_path, wakes)
    try:
        port = server.server_address[1]
        server.last_wake_ts = 0.0  # cooldown due now
        status, body = post(port, "/api/ask", b"{}")
        assert status == 502
        assert "wake" in body.decode().lower() or "إيقاظ" in body.decode()
        assert len(wakes) == 1
        assert wakes[0]["mac"] == "F4:B5:20:46:44:27"
        assert wakes[0]["broadcast"].endswith(".255")

        # immediate second request: cooldown suppresses the second packet
        status2, _ = post(port, "/api/ask", b"{}")
        assert status2 == 502
        assert len(wakes) == 1

        # after cooldown expires, the next failure wakes again
        server.last_wake_ts = time.monotonic() - 1000
        post(port, "/api/ask", b"{}")
        assert len(wakes) == 2
    finally:
        server.shutdown()
        server.server_close()


def test_health_with_pc_up_passes_through_and_never_wakes(catcher_pair):
    server, upstream, wakes = catcher_pair
    port = server.server_address[1]
    server.last_wake_ts = 0.0
    status, body, _ = get(port, "/api/health")
    assert status == 200
    assert json.loads(body)["upstream"] is True
    assert wakes == []
    assert upstream.captured[0][1] == "/api/health"


def test_health_with_pc_down_answers_locally_and_wakes(tmp_path):
    wakes: list = []
    server = make_catcher(None, tmp_path, wakes)
    try:
        port = server.server_address[1]
        server.last_wake_ts = 0.0
        status, body, _ = get(port, "/api/health")
        assert status == 409
        payload = json.loads(body)
        assert payload["status"] == "waking"
        assert payload["local"] is True
        assert len(wakes) == 1

        # cooldown: an immediate poller burst must not re-wake
        server.last_wake_ts = time.monotonic()  # just woke
        status2, body2, _ = get(port, "/api/health")
        assert status2 == 409
        assert len(wakes) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_huge_response_is_truncated_honestly(catcher_pair):
    server, _, _ = catcher_pair
    server.max_bytes = 64
    port = server.server_address[1]
    status, body, _ = get(port, "/big")
    assert status == 200
    assert b"[truncated by aali sentinel]" in body
    assert len(body) < 200  # 64 bytes + marker, not the full 1000


def test_logs_are_content_free(tmp_path):
    """API keys/sids in query strings must never reach the catcher log."""
    wakes: list = []
    server = make_catcher(None, tmp_path, wakes)
    try:
        port = server.server_address[1]
        get(port, "/api/ask?sid=SECRET-SID&x-api-key=SECRET-KEY")
        log_text = (tmp_path / "catcher.log").read_text(encoding="utf-8")
        assert "SECRET-SID" not in log_text
        assert "SECRET-KEY" not in log_text
        assert "/api/ask" in log_text  # the path itself is fine to log
    finally:
        server.shutdown()
        server.server_close()


def test_sentinel_ping_is_local_only(tmp_path):
    """The Pi keepalive probe: answered by the catcher itself - never proxied,
    never wakes the PC, works even with the PC dead."""
    wakes: list = []
    server = make_catcher(None, tmp_path, wakes)
    try:
        port = server.server_address[1]
        status, body, _ = get(port, "/_sentinel/ping")
        assert status == 200
        assert json.loads(body) == {"ok": True, "sentinel": True}
        assert wakes == []
    finally:
        server.shutdown()
        server.server_close()


def test_cooldown_constant_is_stable_contract():
    """45 s: a request burst during boot costs at most one wake packet."""
    assert catcher.WAKE_COOLDOWN_S == 45.0
    assert catcher.STREAM_SUFFIXES == ("/api/ask/stream",)
