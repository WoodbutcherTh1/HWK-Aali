"""The machine registry (pc / mac) read from ``machines.json``.

Each entry may carry::

    {
      "name": "Training PC",
      "ip": "192.168.1.13",
      "mac": "F4:B5:20:46:44:27",
      "user": "hmamk",
      "os": "windows",
      "api_port": 5055
    }

``mac`` may be written with ``-`` or ``:`` separators; it is normalised to
upper-case colon form so the magic-packet builder never has to guess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MAC_CLEAN = re.compile(r"[^0-9A-Fa-f]")


def normalize_mac(mac: str | None) -> str | None:
    """``f4-b5-20-46-44-27`` -> ``F4:B5:20:46:44:27`` (None if unusable)."""
    if not mac:
        return None
    hexed = _MAC_CLEAN.sub("", mac)
    if len(hexed) != 12:
        return None
    return ":".join(hexed[i : i + 2] for i in range(0, 12, 2)).upper()


@dataclass(frozen=True)
class Machine:
    key: str
    name: str
    ip: str
    mac: str | None
    user: str | None
    os: str
    api_port: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ip": self.ip,
            "mac": self.mac,
            "user": self.user,
            "os": self.os,
            "api_port": self.api_port,
        }


class MachineError(ValueError):
    """Raised when machines.json is missing or malformed."""


def _machine(key: str, raw: Any) -> Machine:
    if not isinstance(raw, dict):
        raise MachineError(f"machine '{key}' is not an object")
    ip = str(raw.get("ip") or "").strip()
    if not ip:
        raise MachineError(f"machine '{key}' has no ip")
    mac = normalize_mac(raw.get("mac"))
    port = raw.get("api_port")
    return Machine(
        key=key,
        name=str(raw.get("name") or key.upper()),
        ip=ip,
        mac=mac,
        user=(str(raw["user"]).strip() if raw.get("user") else None),
        os=str(raw.get("os") or "unknown").lower(),
        api_port=int(port) if port else None,
    )


def load_machines(path: str | Path) -> dict[str, Machine]:
    p = Path(path)
    if not p.exists():
        raise MachineError(f"missing {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MachineError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise MachineError(f"{p} has no machines")
    return {k: _machine(k, v) for k, v in data.items()}


# Aliases owners actually type, so "/wake pc" and "/wake training pc" both work.
ALIASES: dict[str, str] = {
    "pc": "pc",
    "desktop": "pc",
    "workstation": "pc",
    "training": "pc",
    "mac": "mac",
    "macbook": "mac",
    "macbook air": "mac",
    "apple": "mac",
    "pi": "pi",
    "raspberry": "pi",
    "all": "all",
    "الpc": "pc",
    "الماك": "mac",
}


def resolve(name: str | None, machines: dict[str, Machine]) -> Machine | None:
    """Turn a user-typed machine name into a Machine, or None if unknown."""
    if not name:
        return None
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    if key in machines:
        return machines[key]
    # allow matching on a prefix of the display name, e.g. "training"
    for k, m in machines.items():
        if m.name.lower().startswith(key):
            return m
    return None