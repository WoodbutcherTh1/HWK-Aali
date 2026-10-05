"""The PC-side control agent — the hands behind Telegram Jarvis.

Why this exists
---------------
Jarvis runs on the Pi and can already ask the Aali brain (:5055) and the voice
server (:5082) over plain HTTP with a shared token. But three capabilities the
router advertises — ``resources``, ``logs`` and ``power`` — had NO working path
to the PC: they were written for SSH, the PC's sshd never materialised (needs
the owner's reboot), and the resource commands were Linux-only anyway, so they
would have been wrong even with SSH. The owner asked to *control* the PC from
Telegram, so those capabilities get the same transport everything else already
uses: a small LAN HTTP service behind a shared-secret token.

The security contract (the voice-server pattern, kept exactly)
-------------------------------------------------------------
* Every ``/api/`` route except ``/api/health`` requires the token in the
  ``X-Aali-Agent-Token`` header, compared with ``hmac.compare_digest``.
* The token lives OUTSIDE the repo (``D:/hwk-data/jarvis_secrets/pc_agent_token.txt``);
  the launcher .bat reads it into the environment so it cannot be forgotten.
  Without a token the dangerous routes REFUSE — an unauthenticated control
  endpoint on the LAN would be worse than no endpoint.
* The endpoint set is CLOSED and every argument is a validated enum or a
  whitelisted name. There is no route that takes a command string: a caller
  can only *select* ``disk`` / ``ram`` / ``gpu``, a log name that already
  exists in the whitelist, or a power action from the fixed table. There is
  no code path from any request body to a shell — the power commands are
  constants keyed by action, mirroring ``jarvis/power.POWER_COMMANDS``.
* ``/api/health`` is open and content-free so a watchdog can probe it without
  holding the token (the brain's health-exemption contract).
* Logs are requested BY NAME and resolved against the files actually present
  in the whitelisted directories — a path with a separator or ``..`` can
  never match, so traversal dies by construction, not by a filter.
* Replies carry numbers and log tails only. The request body is never logged;
  the audit log records the event kind, ok, and byte counts.
* Host header must be an IP literal or ``localhost`` — a DNS-rebinding page
  from the internet presents a domain name and is dropped before routing.

Resource cost: a stdlib ``ThreadingHTTPServer`` that sleeps between requests —
a few MB of RAM, no GPU, no polling loop. The PC does not notice it.
"""

from __future__ import annotations

import argparse
import ctypes
import hmac
import json
import os
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

SERVICE = "pc_agent"
VERSION = "1.0.0"

TOKEN = os.environ.get("AALI_AGENT_TOKEN", "").strip()

# Content-free audit log, next to the other Aali logs.
AUDIT_LOG = Path(os.environ.get("AALI_AGENT_LOG", "D:/hwk-data/pc_agent.log"))

MAX_BODY = 4096  # bytes of JSON is far more than any argument needs

# Where logs may be read from. Requested BY NAME against the files actually
# present here, so a hostile or mistaken path can never escape these roots.
LOG_ROOTS = (Path("D:/hwk-data"), Path("D:/hwk-data/soup"))
MAX_TAIL_LINES = 200
DEFAULT_TAIL_LINES = 40
MAX_LINE_CHARS = 4000

# The fixed power table — the same commands jarvis/power.py uses for windows,
# kept in sync by comment. A request names an action; the command is a constant.
POWER_COMMANDS: dict[str, str] = {
    "shutdown": "shutdown /s /t 5",
    "reboot": "shutdown /r /t 5",
    "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
}

Runner = Callable[[list[str], int], tuple[int, str]]


def log_event(event: str, **fields: Any) -> None:
    """Append one content-free JSONL record; a logging failure never raises."""
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **fields}
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ------------------------------------------------------------ collectors
def collect_disk() -> dict[str, Any]:
    """Free/total for every fixed drive that exists (read-only, no subprocess)."""
    drives: dict[str, Any] = {}
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        try:
            total, _used, free = shutil.disk_usage(root)
        except OSError:
            continue
        drives[letter] = {
            "total_gb": round(total / 2**30, 1),
            "free_gb": round(free / 2**30, 1),
            "free_pct": round(100.0 * free / total, 1) if total else 0.0,
        }
    return {"ok": True, "drives": drives}


def collect_ram() -> dict[str, Any]:
    """Physical memory via GlobalMemoryStatusEx (stdlib ctypes)."""

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            raise OSError("GlobalMemoryStatusEx failed")
    except Exception as exc:  # honest refusal, not a fake number
        return {"ok": False, "error": type(exc).__name__}
    return {
        "ok": True,
        "total_gb": round(stat.ullTotalPhys / 2**30, 1),
        "avail_gb": round(stat.ullAvailPhys / 2**30, 1),
        "load_pct": stat.dwMemoryLoad,
    }


def collect_gpu(runner: Runner | None = None) -> dict[str, Any]:
    """One nvidia-smi query; an absent tool is reported, never faked."""
    run = runner or _run
    argv = [
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    code, out = run(argv, 10)
    if code != 0 or not out.strip():
        return {"ok": False, "error": "nvidia-smi unavailable"}
    line = out.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        return {"ok": False, "error": "unexpected nvidia-smi output"}
    try:
        gpus = [
            {
                "name": parts[0],
                "util_pct": float(parts[1]),
                "mem_used_gb": round(float(parts[2]) / 1024, 1),
                "mem_total_gb": round(float(parts[3]) / 1024, 1),
                "temp_c": float(parts[4]) if len(parts) > 4 and parts[4] else None,
            }
        ]
    except ValueError:
        return {"ok": False, "error": "unexpected nvidia-smi values"}
    return {"ok": True, "gpus": gpus}


def _run(argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, errors="replace"
        )
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except FileNotFoundError as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ----------------------------------------------------------------- logs
def whitelisted_logs() -> list[str]:
    """Log file names (no paths) present in the whitelisted roots."""
    names: set[str] = set()
    for root in LOG_ROOTS:
        try:
            for p in root.glob("*.log"):
                if p.is_file():
                    names.add(p.name)
        except OSError:
            continue
    return sorted(names)


def tail_log(name: str, lines: int = DEFAULT_TAIL_LINES) -> dict[str, Any]:
    """Tail ONE whitelisted log by name. Traversal cannot reach this by design."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return {"ok": False, "error": "log name must be a bare file name"}
    if name not in whitelisted_logs():
        return {"ok": False, "error": f"unknown log '{name[:80]}'", "known": whitelisted_logs()}
    lines = max(1, min(int(lines or DEFAULT_TAIL_LINES), MAX_TAIL_LINES))
    for root in LOG_ROOTS:
        path = root / name
        if path.is_file():
            break
    else:  # pragma: no cover - whitelisted_logs() found it a moment ago
        return {"ok": False, "error": "log vanished"}
    try:
        with path.open("rb") as fh:
            data = fh.read()[-(MAX_LINE_CHARS * MAX_TAIL_LINES):]
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}
    tail = [ln for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()]
    return {"ok": True, "file": name, "lines": tail[-lines:]}


# ---------------------------------------------------------------- power
def power_action(action: str, runner: Runner | None = None) -> dict[str, Any]:
    """Run ONE fixed command from the table. Unknown actions are refused."""
    cmd = POWER_COMMANDS.get((action or "").strip().lower())
    if cmd is None:
        return {"ok": False, "error": f"unknown action '{action[:60]}'"}
    run = runner or _run
    code, out = run(cmd.split(), 15)
    return {"ok": code == 0, "action": action, "exit": code, "detail": out[-200:]}


# ---------------------------------------------------------------- server
def host_allowed(host_header: str) -> bool:
    """Only IP literals / localhost pass — a rebinding domain is refused."""
    host = (host_header or "").split(":")[0].strip().lower()
    if not host or host in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return True
    if host.startswith("[") and host.endswith("]"):  # IPv6 literal
        return True
    return host.replace(".", "").isdigit()  # IPv4 literal only


class AgentHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE}/{VERSION}"

    # Content-free access log: never the query string, never the body.
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _json(self, obj: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not TOKEN:
            return False  # unconfigured token = refuse everything, fail closed
        got = self.headers.get("X-Aali-Agent-Token", "")
        return hmac.compare_digest(got, TOKEN)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("payload too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not host_allowed(self.headers.get("Host", "")):
            return self._json({"ok": False, "error": "bad host"}, 403)
        if path == "/api/health":
            return self._json({"ok": True, "service": SERVICE, "version": VERSION})
        if path.startswith("/api/") and not self._authorized():
            log_event("unauthorized", method="GET", path=path)
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not host_allowed(self.headers.get("Host", "")):
            return self._json({"ok": False, "error": "bad host"}, 403)
        if path == "/api/health":
            return self._json({"ok": True, "service": SERVICE, "version": VERSION})
        if not self._authorized():
            log_event("unauthorized", method="POST", path=path)
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        try:
            body = self._body()
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # malformed JSON etc.
            return self._json({"ok": False, "error": type(exc).__name__}, 400)
        try:
            if path == "/api/resources":
                return self._resources(body)
            if path == "/api/logs":
                return self._logs(body)
            if path == "/api/power":
                return self._power(body)
        except Exception as exc:  # honest failure, never a silent 200
            log_event("handler_error", path=path, reason=type(exc).__name__)
            return self._json({"ok": False, "error": f"{type(exc).__name__}"}, 500)
        return self._json({"ok": False, "error": "not found"}, 404)

    def _resources(self, body: dict[str, Any]) -> None:
        what = str(body.get("what") or "all").strip().lower()
        allowed = {"disk", "ram", "gpu", "all"}
        if what not in allowed:
            return self._json({"ok": False, "error": f"what must be one of {sorted(allowed)}"}, 400)
        out: dict[str, Any] = {"ok": True, "what": what, "host": os.environ.get("COMPUTERNAME", "pc")}
        if what in ("disk", "all"):
            out["disk"] = collect_disk()
        if what in ("ram", "all"):
            out["ram"] = collect_ram()
        if what in ("gpu", "all"):
            out["gpu"] = collect_gpu()
        log_event("resources", what=what)
        return self._json(out)

    def _logs(self, body: dict[str, Any]) -> None:
        name = str(body.get("file") or "").strip()
        try:
            lines = int(body.get("lines") or DEFAULT_TAIL_LINES)
        except (TypeError, ValueError):
            lines = DEFAULT_TAIL_LINES
        result = tail_log(name, lines)
        log_event("logs", ok=bool(result.get("ok")), lines=len(result.get("lines") or []))
        return self._json(result, 200 if result.get("ok") else 404)

    def _power(self, body: dict[str, Any]) -> None:
        action = str(body.get("action") or "").strip().lower()
        result = power_action(action)
        log_event("power", action=action, ok=bool(result.get("ok")))
        return self._json(result, 200 if result.get("ok") else 400)


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), AgentHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=SERVICE, description="Jarvis PC control agent")
    parser.add_argument("--host", default=os.environ.get("AALI_AGENT_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AALI_AGENT_PORT", "5083")))
    args = parser.parse_args(argv)

    # UTF-8 streams: the repo convention (Arabic log tails must never die on a
    # cp1252 console).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    if not TOKEN:
        print(
            f"[{SERVICE}] refusing to start: AALI_AGENT_TOKEN is not set. "
            "Start via scripts/pc_agent.bat, which reads the token from disk.",
            file=sys.stderr,
        )
        return 2

    httpd = make_server(args.host, args.port)
    log_event("start", port=args.port, bind=args.host, version=VERSION)
    print(f"[{SERVICE}] listening on {args.host}:{args.port} (token required on /api/*)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        log_event("stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
