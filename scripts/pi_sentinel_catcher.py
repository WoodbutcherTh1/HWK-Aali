#!/usr/bin/env python3
"""Aali Pi sentinel catcher - runs ON the Raspberry Pi (stdlib only).

Role in the WoL sentinel design (tasks/pi-wol-sentinel.md):

    Internet -> Cloudflare edge -> cloudflared tunnel (on the Pi)
             -> THIS catcher (127.0.0.1:8766 on the Pi)
             -> http://<pc-ip>:5055   (Aali API on the home PC)

The Pi is always on (~3 W). The home PC sleeps when idle. This catcher:

1. Forwards every request to the PC verbatim, streaming both ways
   (SSE /api/ask/stream must never be buffered).
2. Answers GET /api/health LOCALLY when the PC is asleep, without waking
   it: status 409 + a bilingual "heating up" body - iOS/web clients read
   the body instead of hanging on a dead origin.
3. Wakes the PC with a Wake-on-LAN magic packet (directed + broadcast)
   when the PC is unreachable, subject to a cooldown so a burst of
   requests costs at most one wake per WAKE_COOLDOWN_S.
4. Never logs request bodies, query strings, or reply content - the log
   lines are method + trimmed path + status + byte counts only.

Run it under the aalici user's cron (@reboot) via scripts/pi_sentinel/
run_catcher.sh. No root, no services, no linger dependency: Debian cron
runs user crontab jobs with no login required.
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

# Re-wake attempts are bounded: at most one wake every 45 s no matter
# how many requests arrive while the PC is down/sleeping.
WAKE_COOLDOWN_S = 45.0
# Per-socket timeout for normal (non-stream) proxied requests.
PROXY_TIMEOUT_S = 30.0
# SSE streams may legitimately go quiet during a long tool call; the
# brain emits tool-activity events well inside this window.
STREAM_READ_TIMEOUT_S = 75.0
# Response bodies larger than this are cut with an honest marker.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
# Hop-by-hop / unsafe-to-copy headers when proxying responses.
_SKIP_RESPONSE_HEADERS = {
    "connection", "transfer-encoding", "content-encoding",
    "content-length", "keep-alive", "alt-svc", "server",
}
_COPY_REQUEST_HEADERS = {
    "content-type", "accept", "accept-language", "user-agent",
    "x-api-key", "x-session-token", "authorization", "x-requested-with",
    "cache-control",
}

STREAM_SUFFIXES = ("/api/ask/stream",)
STREAM_CONTENT_TYPE = "text/event-stream"


def build_magic_packet(mac: str) -> bytes:
    """6x 0xFF + the MAC repeated 16 times - the Wake-on-LAN standard frame."""
    hex_part = mac.replace(":", "").replace("-", "").replace(".", "").strip()
    if len(hex_part) != 12 or any(c not in "0123456789abcdefABCDEF" for c in hex_part):
        raise ValueError(f"not a valid MAC address: {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(hex_part) * 16


def wake_up(pc_ip: str, mac: str, wake_port: int = 9, broadcast: str | None = None,
            retries: int = 3) -> bool:
    """Send a magic packet to the PC directly and (by default) as a subnet
    broadcast - sleeping NICs answer either, depending on driver state."""
    packet = build_magic_packet(mac)
    targets = [pc_ip] + ([broadcast] if broadcast else [])
    sent = False
    for _ in range(retries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                for target in targets:
                    try:
                        sock.sendto(packet, (target, wake_port))
                        sent = True
                    except OSError:
                        continue
        except OSError:
            continue
        if sent:
            break
        time.sleep(0.25)
    return sent


def _probe_up(pc_ip: str, port: int, timeout: float = 4.0) -> bool:
    """Cheap TCP reachability check for the PC's API port."""
    try:
        with socket.create_connection((pc_ip, port), timeout=timeout):
            return True
    except OSError:
        return False


class SentinelServer(ThreadingHTTPServer):
    """Catcher server state: wake bookkeeping + an injectable wake_fn
    (tests replace it with a recorder; it never sends real packets there)."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler],
                 pc_ip: str, pc_port: int, pc_mac: str | None,
                 log_file: str | None, max_bytes: int,
                 wake_fn: Callable[..., bool] = wake_up) -> None:
        super().__init__(addr, handler)
        self.pc_ip = pc_ip
        self.pc_port = pc_port
        self.pc_mac = pc_mac
        self.log_file = log_file
        self.max_bytes = max_bytes
        self.wake_fn = wake_fn
        self.last_wake_ts = 0.0
        self.wake_lock = threading.Lock()


class CatcherHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # connection-per-response: streaming without chunked
    server_version = "AaliSentinel/1.0"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """Swallow the framework's default lines: they carry the RAW request
        line (query string included) and would break the content-free rule."""
        pass

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Content-free per-response line: method + trimmed path + status."""
        self._note('"%s %s" %s', self.command, self._safe_path(), code)

    def _note(self, fmt: str, *args: Any) -> None:
        """Append one content-free line to the catcher log file."""
        server = self.server
        assert isinstance(server, SentinelServer)
        if not server.log_file:
            return
        try:
            with open(server.log_file, "a", encoding="utf-8") as fh:
                fh.write(f"{self.log_date_time_string()} {fmt % args}\n")
        except OSError:
            pass  # a broken log must never break serving

    def _safe_path(self) -> str:
        """Path WITHOUT the query string (keys/tokens must not reach logs)."""
        path = urlsplit(self.path).path or "/"
        return path[:100]

    def _is_stream(self) -> bool:
        if urlsplit(self.path).path in STREAM_SUFFIXES:
            return True
        return STREAM_CONTENT_TYPE in (self.headers.get("Accept") or "")

    # -- responses --------------------------------------------------------

    def _send_json(self, code: int, payload: dict[str, Any],
                   extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client hung up - nothing to answer anymore

    def _health_down(self) -> None:
        """Bilingual 409: PC asleep/absent. Clients must read the body
        instead of hanging on a dead origin."""
        self._send_json(409, {
            "ok": False,
            "local": True,
            "status": "waking",
            "reply": "آلي يستيقظ الآن - الكمبيوتر الرئيسي نائم وتُرسل حزمة إيقاظ إليه، "
                     "أعد المحاولة بعد نصف دقيقة. "
                     "(Home PC is asleep; a wake packet was sent - retry in ~30s.)",
        })

    # -- proxy ------------------------------------------------------------

    def _forward(self, method: str) -> None:  # noqa: C901
        server = self.server
        assert isinstance(server, SentinelServer)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None

        req = urllib.request.Request(
            f"http://{server.pc_ip}:{server.pc_port}{self.path}",
            data=body, method=method,
        )
        for name in _COPY_REQUEST_HEADERS:
            value = self.headers.get(name)
            if value:
                req.add_header(name, value)
        if body is not None and not req.has_header("Content-type"):
            req.add_header("Content-Type",
                           self.headers.get("Content-Type") or "application/octet-stream")

        stream = self._is_stream()
        timeout = STREAM_READ_TIMEOUT_S if stream else PROXY_TIMEOUT_S
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed LAN origin
        except urllib.error.HTTPError as err:
            resp = err  # 4xx/5xx from the PC are forwarded as-is
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            # PC unreachable: maybe asleep -> wake (cooldown-gated) + honest 502.
            self._maybe_wake()
            self._send_json(502, {
                "ok": False,
                "status": "unreachable",
                "reply": "الكمبيوتر الرئيسي غير متاح حالياً - أُرسلت حزمة إيقاظ، "
                         "أعد المحاولة بعد نصف دقيقة. (Home PC unreachable; wake "
                         "packet sent - retry in ~30s.)",
            }, {"Retry-After": "15"})
            self._note('502 "unreachable %s %s"', method, self._safe_path())
            return

        status = getattr(resp, "status", resp.code)  # type: ignore[attr-defined]
        ctype = resp.headers.get("Content-Type") or ""
        streaming = stream or STREAM_CONTENT_TYPE in ctype

        if streaming:
            self.send_response(status)
            for name, value in resp.headers.items():
                if name.lower() not in _SKIP_RESPONSE_HEADERS:
                    self.send_header(name, value)
            self.end_headers()
            self.close_connection = True
            sent = 0
            try:
                while True:
                    chunk = resp.read(512)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    sent += len(chunk)
            except (BrokenPipeError, ConnectionResetError, socket.timeout,
                    TimeoutError, OSError):
                pass  # either side dropped the stream; nothing to salvage
            self._note('stream "%s %s" sent=%d', method, self._safe_path(), sent)
            resp.close()
            return

        data = resp.read(server.max_bytes + 1)
        truncated = False
        if len(data) > server.max_bytes:
            data = data[:server.max_bytes]
            truncated = True
        resp.close()

        self.send_response(status)
        for name, value in resp.headers.items():
            if name.lower() not in _SKIP_RESPONSE_HEADERS:
                self.send_header(name, value)
        if truncated:
            data += b"\n...[truncated by aali sentinel]\n"
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self._note('proxy "%s %s" %s bytes=%d%s',
                   method, self._safe_path(), status, len(data),
                   " truncated" if truncated else "")

    def _maybe_wake(self) -> None:
        """Send a wake packet if the cooldown allows it; remember the attempt."""
        server = self.server
        assert isinstance(server, SentinelServer)
        if not server.pc_mac:
            return
        now = time.monotonic()
        with server.wake_lock:
            if now - server.last_wake_ts < WAKE_COOLDOWN_S:
                return
            server.last_wake_ts = now
        sent = server.wake_fn(
            server.pc_ip, server.pc_mac,
            broadcast=f"{server.pc_ip.rsplit('.', 1)[0]}.255",
        )
        self._note("wake packet sent to %s: %s", server.pc_ip,
                   "ok" if sent else "FAILED")

    # -- verbs ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._route("PATCH")

    def _route(self, method: str) -> None:
        server = self.server
        assert isinstance(server, SentinelServer)
        path = urlsplit(self.path).path or "/"

        # Local-only liveness probe for the Pi's own keepalive loop: never
        # proxies, never wakes the PC, never touches the LAN.
        if path == "/_sentinel/ping":
            self._send_json(200, {"ok": True, "sentinel": True})
            return

        # Local-first health: a sleeping PC must never be woken by a
        # poller - answer from the catcher itself.
        if path == "/api/health":
            now = time.monotonic()
            due = now - server.last_wake_ts >= WAKE_COOLDOWN_S
            if due and _probe_up(server.pc_ip, server.pc_port):
                self._forward(method)  # PC is awake; pass through untouched
                return
            if due:
                self._maybe_wake()
            self._health_down()
            return

        self._forward(method)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aali Pi sentinel catcher")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--target", default="http://192.168.1.117:5055",
                        help="home PC API base URL")
    parser.add_argument("--pc-mac", default="F4:B5:20:46:44:27",
                        help="home PC NIC MAC for Wake-on-LAN")
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)

    target = urlsplit(args.target)
    pc_ip = target.hostname or "127.0.0.1"
    pc_port = target.port or 80

    server = SentinelServer(
        (args.bind, args.port), CatcherHandler,
        pc_ip=pc_ip, pc_port=pc_port, pc_mac=args.pc_mac,
        log_file=args.log_file, max_bytes=args.max_bytes,
    )
    print(f"aali sentinel catcher on {args.bind}:{args.port} -> {pc_ip}:{pc_port}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
