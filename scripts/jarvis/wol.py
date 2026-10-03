"""Wake-on-LAN: build and send a magic packet.

Pure stdlib so it works on the Pi with no extra packages and no root. The
sender is injectable so tests can assert the exact bytes without touching the
network.
"""

from __future__ import annotations

import socket
from typing import Callable, Sequence

from .machines import normalize_mac

PORT = 9


def magic_packet(mac: str) -> bytes:
    """Build a magic packet: 6x 0xFF then the MAC repeated 16 times."""
    normalized = normalize_mac(mac)
    if not normalized:
        raise ValueError(f"not a usable MAC address: {mac!r}")
    raw = bytes.fromhex(normalized.replace(":", ""))
    return b"\xff" * 6 + raw * 16


def send_magic_packet(
    mac: str,
    broadcast: str = "192.168.1.255",
    port: int = PORT,
    sender: Callable[[bytes, tuple], None] | None = None,
) -> bytes:
    """Send a magic packet. Returns the bytes that were sent.

    ``sender`` defaults to a plain UDP sendto; tests pass their own recorder.
    """
    packet = magic_packet(mac)
    if sender is None:

        def sender(data: bytes, address: tuple) -> None:  # type: ignore[misc]
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(data, address)
            finally:
                sock.close()

    sender(packet, (broadcast, port))
    return packet


def send_to_many(
    macs: Sequence[str],
    broadcast: str = "192.168.1.255",
    port: int = PORT,
    sender: Callable[[bytes, tuple], None] | None = None,
) -> dict[str, bool]:
    """Fan one packet out to several MACs; report per-MAC success."""
    result: dict[str, bool] = {}
    for mac in macs:
        if not mac:
            continue
        try:
            send_magic_packet(mac, broadcast=broadcast, port=port, sender=sender)
            result[normalize_mac(mac) or mac] = True
        except Exception:
            result[mac] = False
    return result