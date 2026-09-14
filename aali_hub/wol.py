"""Wake-on-LAN sender — wakes the brain PC when a request arrives.

Builds the standard WoL magic packet (6×0xFF followed by the MAC repeated 16×)
and broadcasts it over UDP. No external dependencies: raw sockets only.
MAC lookup is intentionally manual (``AALI_WOL_MAC`` env) — auto-discovery
across the internet is not a thing WoL supports anyway; see
docs/wake_on_lan_setup.md for BIOS/NIC configuration.
"""

from __future__ import annotations

import socket
import struct

__all__ = ["parse_mac", "magic_packet", "send_wol", "WolError"]


class WolError(Exception):
    """Expected WoL error (bad MAC, send failure)."""


def parse_mac(mac: str) -> bytes:
    """Parse 'AA:BB:CC:DD:EE:FF' (also accepts '-' or no separators)."""
    cleaned = (mac or "").strip().replace(":", "").replace("-", "").lower()
    if len(cleaned) != 12:
        raise WolError(f"bad MAC address: {mac!r}")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise WolError(f"bad MAC address: {mac!r}") from exc


def magic_packet(mac: str) -> bytes:
    """Build the 102-byte WoL magic packet for *mac*."""
    mac_bytes = parse_mac(mac)
    return b"\xff" * 6 + mac_bytes * 16


def send_wol(mac: str, *, broadcast: str = "255.255.255.255",
             port: int = 9, repeat: int = 3) -> int:
    """Broadcast a WoL packet; returns packets sent. Raises WolError on failure.

    Repeats the send a few times — NICs occasionally drop single datagrams.
    """
    packet = magic_packet(mac)
    sent = 0
    last_error: Exception | None = None
    for _ in range(max(1, repeat)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.sendto(packet, (broadcast, port))
            sent += 1
        except OSError as exc:
            last_error = exc
        finally:
            sock.close()
    if sent == 0 and last_error is not None:
        raise WolError(f"failed to send WoL packet: {last_error}") from last_error
    return sent
