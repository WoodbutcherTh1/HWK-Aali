"""Remote power control and status probing.

Two transports:

* **SSH** for anything that changes machine state (shutdown / reboot / sleep).
  The command is built per-OS and the subprocess is injectable for tests.
* **Plain TCP/HTTP probes** for status, so ``/status`` still answers on a
  machine where SSH is not set up yet.

Every remote action is deliberately narrow: the remote command is chosen from a
fixed table keyed by OS, never built from user text. A Telegram message can
therefore only ever select an action, never inject a command.
"""

from __future__ import annotations

import shlex
import socket
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from .machines import Machine

SSH_TIMEOUT = 20

# Fixed command table. Values are (remote_command, human description).
POWER_COMMANDS: dict[str, dict[str, str]] = {
    "windows": {
        "shutdown": "shutdown /s /t 5",
        "reboot": "shutdown /r /t 5",
        "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
        "logoff": "shutdown /l",
    },
    "macos": {
        "shutdown": "osascript -e 'tell app \"System Events\" to shut down'",
        "reboot": "osascript -e 'tell app \"System Events\" to restart'",
        "sleep": "pmset sleepnow",
        "logoff": "osascript -e 'tell app \"System Events\" to log out'",
    },
    "linux": {
        "shutdown": "sudo -n shutdown -h now",
        "reboot": "sudo -n reboot",
        "sleep": "sudo -n systemctl suspend",
        "logoff": "sudo -n systemctl poweroff",
    },
}

ACTIONS = ("shutdown", "reboot", "sleep", "logoff")


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    detail: str
    code: int = 0


def _runner(argv: list[str], timeout: int) -> tuple[int, str]:
    """Run argv, return (returncode, combined output tail)."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except FileNotFoundError as exc:
        return 127, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()[-400:]


def ssh_exec(
    machine: Machine,
    command: str,
    runner: Callable[[list[str], int], tuple[int, str]] | None = None,
    timeout: int = SSH_TIMEOUT,
) -> ActionResult:
    """Run one command on a machine over SSH.

    ``BatchMode=yes`` keeps it non-interactive: if a key is missing we get a
    clear failure instead of a hang on a password prompt.
    """
    if not machine.user:
        return ActionResult(False, f"{machine.key}: no user configured")
    run = runner or _runner
    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{machine.user}@{machine.ip}",
        command,
    ]
    code, out = run(argv, timeout)
    return ActionResult(code == 0, out or f"exit {code}", code)


def power_action(
    machine: Machine,
    action: str,
    runner: Callable[[list[str], int], tuple[int, str]] | None = None,
) -> ActionResult:
    """Shut down / reboot / sleep a machine. Refuses unknown OS or action."""
    action = action.lower().strip()
    if action not in ACTIONS:
        return ActionResult(False, f"unknown action '{action}'")
    table = POWER_COMMANDS.get(machine.os)
    if table is None:
        return ActionResult(False, f"no power commands known for os '{machine.os}'")
    return ssh_exec(machine, table[action], runner=runner)


def tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ping_ok(host: str, count: int = 1, timeout: int = 2) -> bool:
    code, _ = _runner(["ping", "-n", str(count), "-w", str(timeout * 1000), host], timeout + 3)
    return code == 0


def machine_status(machine: Machine, pinger: Callable[[str, int, int], bool] = ping_ok) -> dict[str, Any]:
    """Online check plus the brain API check when a port is configured."""
    online = bool(pinger(machine.ip, 1, 2))
    api = None
    if online and machine.api_port:
        api = tcp_open(machine.ip, machine.api_port)
    return {
        "key": machine.key,
        "name": machine.name,
        "ip": machine.ip,
        "os": machine.os,
        "online": online,
        "api": api,
    }


def describe_status(status: dict[str, Any]) -> str:
    """One Arabic-first line for a machine status dict."""
    if status.get("online"):
        api = status.get("api")
        if api is True:
            return f"🟢 {status['name']} — متصل، والعقل يعمل ({status['ip']})"
        if api is False:
            return f"🟡 {status['name']} — متصل، لكن العقل لا يستجيب ({status['ip']})"
        return f"🟢 {status['name']} — متصل ({status['ip']})"
    return f"🔴 {status['name']} — غير متصل ({status['ip']})"


def shell_quote(text: str) -> str:
    """Quote for a remote shell. Used for fixed reads, never user input."""
    return shlex.quote(text)