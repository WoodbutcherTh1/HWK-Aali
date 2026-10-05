"""Talk to the PC control agent (``pc_agent.py`` on the PC, :5083).

Why HTTP and not SSH
--------------------
The three capabilities that need to *act on the PC* — ``resources``, ``logs``
and ``power`` — were written for SSH, but the PC's sshd never materialised and
an SSH pipe mangles arbitrary text through a remote shell (the quoting lesson
that moved the brain's ``/ask`` to HTTP). The PC now runs the same kind of
small token-gated service the brain and the voice server already are: a closed
endpoint set where every argument is a validated enum or a whitelisted log
name, and the power commands are constants — a request can only ever *select*
an action, never supply one.

The token file is OPTIONAL, like every other Jarvis secret: absent means the
agent path refuses honestly and the handler falls back to SSH (which is how
non-agent machines like the Mac keep working). Nothing here ever prints the
token.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .machines import Machine

# Kept in step with pc_agent.py's default.
DEFAULT_AGENT_PORT = 5083
TIMEOUT = 20
MAX_BODY = 4096

TOKEN_FILE = Path.home() / ".aali" / "jarvis" / "pc_agent_token.txt"


def load_token(cfg_dir: Path | None = None) -> str:
    """The PC agent's shared token, or ``""`` when the owner has not set it."""
    f = (cfg_dir or TOKEN_FILE.parent) / "pc_agent_token.txt"
    try:
        return f.read_text(encoding="utf-8", errors="replace").strip() if f.exists() else ""
    except OSError:
        return ""


def api_base(machine: Machine) -> str:
    """The agent's base URL for a machine (its agent_port, else 5083)."""
    return f"http://{machine.ip}:{machine.agent_port or DEFAULT_AGENT_PORT}"


def has_agent(machine: Machine) -> bool:
    """Does this machine advertise a control agent?"""
    return bool(machine.agent_port)


def call(
    machine: Machine,
    path: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = TIMEOUT,
) -> tuple[bool, dict[str, Any]]:
    """One POST to the agent. Returns ``(ok, data_or_reason)``. Never raises.

    ``data`` always carries ``error`` on failure — a short English reason the
    caller frames in Arabic, exactly like :mod:`brain`.
    """
    key = token if token is not None else load_token()
    if not key:
        return False, {"ok": False, "error": "no agent token configured"}
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base(machine)}{path}",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Aali-Agent-Token": key,
            "User-Agent": "jarvis/1.0 (+aali)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # A 4xx body may still carry the honest reason and, for /api/logs, the
        # whitelist of known files — read it before reducing it to a code.
        try:
            data = json.loads(exc.read().decode("utf-8", "replace"))
            if isinstance(data, dict):
                data.setdefault("ok", False)
                data.setdefault("error", f"HTTP {exc.code}")
                return False, data
        except Exception:
            pass
        return False, {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        # The PC may be asleep, rebooting, or the agent not installed yet.
        return False, {"ok": False, "error": type(exc).__name__}
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return False, {"ok": False, "error": "reply was not JSON"}
    if not isinstance(data, dict):
        return False, {"ok": False, "error": "reply was not an object"}
    return bool(data.get("ok")), data


def health(machine: Machine, timeout: int = 5) -> bool:
    """Open, content-free liveness probe (the watchdog contract)."""
    try:
        with urllib.request.urlopen(f"{api_base(machine)}/api/health", timeout=timeout) as resp:
            return bool(json.loads(resp.read().decode("utf-8", "replace")).get("ok"))
    except Exception:
        return False


def resources(machine: Machine, what: str) -> tuple[bool, dict[str, Any]]:
    """Disk / RAM / GPU numbers for the PC."""
    return call(machine, "/api/resources", {"what": what})


def logs(machine: Machine, name: str, lines: int = 40) -> tuple[bool, dict[str, Any]]:
    """Tail one whitelisted log on the PC, by bare file name."""
    return call(machine, "/api/logs", {"file": name, "lines": lines})


def power(machine: Machine, action: str) -> tuple[bool, dict[str, Any]]:
    """Run one fixed power action on the PC (shutdown/reboot/sleep)."""
    return call(machine, "/api/power", {"action": action})
