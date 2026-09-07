"""Machine operations for the HWK agent: Aali's hands on the PC.

Wrapped by file_agent/file_tools.py::machine_ops in a subprocess. Capabilities
requested by the owner (2026-09-06): open apps, install/uninstall apps, list
and stop processes, inspect the system. Irreversible actions (install,
uninstall, kill) require force=true from the tool layer AND are gated by the
agent's always_ask confirmation policy; Aali's own training runtimes
(python/node) are protected from kill_process by name.

Usage:
  python scripts/machine_ops.py open --target "notepad.exe"
  python scripts/machine_ops.py install --target "OpenJS.NodeJS.LTS" --engine winget
  python scripts/machine_ops.py list_processes --name chrome
  python scripts/machine_ops.py kill_process --pid 1234 --force
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Aali's own runtimes: never killed by name (the 12h-training-killed-by-a-kill
# rule in AGENTS.md). PID kills of these images are also refused.
PROTECTED_IMAGES = {"python.exe", "pythonw.exe", "node.exe"}


def _fail(message: str) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False))
    sys.exit(1)


def _ok(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


def cmd_open(args: argparse.Namespace) -> None:
    """Open a file, folder, URL, or app with the OS default handler."""
    target = args.target.strip()
    if not target:
        _fail("target is required")
    lowered = target.lower()
    if lowered.startswith(("http://", "https://")):
        os.startfile(target)  # noqa: S606 - OS default browser, user-requested
        _ok({"opened": target, "how": "startfile"})
        return
    # Known Windows apps by common name, resolved via PATH or App Paths.
    if Path(target).suffix.lower() in {".exe", ".lnk"}:
        resolved = shutil.which(target)
        if not resolved:
            for candidate in (
                Path("C:/Program Files") / target,
                Path("C:/Program Files (x86)") / target,
            ):
                if candidate.exists():
                    resolved = str(candidate)
                    break
        if not resolved:
            _fail(f"executable not found: {target}")
        subprocess.Popen([resolved], cwd=str(Path(resolved).parent))  # noqa: S603
        _ok({"opened": resolved, "how": "popen"})
        return
    candidate = Path(target)
    if candidate.exists():
        os.startfile(str(candidate))  # noqa: S606
        _ok({"opened": str(candidate), "how": "startfile"})
        return
    # Fall through to ShellExecute semantics via cmd start (apps registered
    # without a PATH entry: 'start excel', 'start winword', ...).
    code, out, err = _run(["cmd", "/c", "start", "", target], timeout=30)
    if code != 0:
        _fail(f"could not open '{target}': {err or out or 'unknown error'}")
    _ok({"opened": target, "how": "shell"})


def _winget(args: argparse.Namespace) -> None:
    winget = shutil.which("winget")
    if not winget:
        _fail("winget not available on this machine")
    if args.action == "install":
        cmd = [winget, "install", "--id", args.target, "--exact",
               "--accept-source-agreements", "--accept-package-agreements"]
        if args.silent:
            cmd += ["--silent"]
    elif args.action == "uninstall":
        cmd = [winget, "uninstall", "--id", args.target, "--exact", "--silent"]
    else:  # search
        cmd = [winget, "search", args.target]
    code, out, err = _run(cmd, timeout=900)
    tail = (out or err or "").strip().splitlines()[-12:]
    if code != 0:
        _fail(f"winget {args.action} failed (exit {code}): " + " | ".join(tail)[-400:])
    _ok({"action": args.action, "package": args.target, "engine": "winget", "exit_code": 0,
         "output_tail": tail})


def _npm(args: argparse.Namespace) -> None:
    npm = shutil.which("npm") or r"C:\Program Files\nodejs\npm.cmd"
    if args.action == "install":
        cmd = [npm, "install", "-g", args.target]
    elif args.action == "uninstall":
        cmd = [npm, "uninstall", "-g", args.target]
    else:
        cmd = [npm, "search", args.target, "--no-update-notifier"]
    code, out, err = _run(cmd, timeout=600)
    tail = (out or err or "").strip().splitlines()[-12:]
    if code != 0:
        _fail(f"npm {args.action} failed (exit {code}): " + " | ".join(tail)[-400:])
    _ok({"action": args.action, "package": args.target, "engine": "npm", "exit_code": 0,
         "output_tail": tail})


def cmd_install(args: argparse.Namespace) -> None:
    if not args.force:
        _fail("install requires force=true (irreversible system change; the agent "
              "must obtain explicit user confirmation first)")
    if args.engine == "auto":
        # Node/npm packages go to npm, everything else to winget.
        args.engine = "npm" if (args.target.endswith(("-cli", "code")) and "/" not in args.target
                                and args.target.startswith("@")) or args.target.startswith("@") else "winget"
    if args.engine == "winget":
        _winget(args)
    elif args.engine == "npm":
        _npm(args)
    else:
        _fail("engine must be auto, winget, or npm")


def cmd_uninstall(args: argparse.Namespace) -> None:
    if not args.force:
        _fail("uninstall requires force=true (irreversible system change; the agent "
              "must obtain explicit user confirmation first)")
    if args.engine == "auto":
        args.engine = "winget"
    if args.engine == "winget":
        _winget(args)
    elif args.engine == "npm":
        _npm(args)
    else:
        _fail("engine must be auto, winget, or npm")


def cmd_search_software(args: argparse.Namespace) -> None:
    """Read-only: find the right winget/npm package id before installing."""
    if args.engine in ("winget", "auto"):
        _winget(args)
    else:
        _npm(args)


def cmd_list_processes(args: argparse.Namespace) -> None:
    code, out, err = _run(["tasklist", "/FO", "CSV", "/NH"], timeout=60)
    if code != 0:
        _fail("tasklist failed: " + err[-300:])
    rows = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 5:
            continue
        name, pid, mem = parts[0], parts[1], parts[-1].rstrip('K"').strip()
        if args.name and args.name.lower() not in name.lower():
            continue
        rows.append({"name": name, "pid": int(pid) if pid.isdigit() else pid,
                     "memory": mem + " K"})
    rows = rows[: args.max_results]
    _ok({"count": len(rows), "processes": rows, "filter": args.name or None})


def cmd_kill_process(args: argparse.Namespace) -> None:
    if not args.force:
        _fail("kill_process requires force=true (irreversible; confirm with the user first)")
    if args.pid is None and not args.name:
        _fail("kill_process needs --pid or --name")
    if args.name:
        safe = args.name.lower()
        if any(safe == p or safe.endswith(p) for p in PROTECTED_IMAGES):
            _fail(f"refusing to kill '{args.name}': protected runtime (Aali's training/"
                  "agent processes). Kill it by exact PID only if you are certain.")
        code, out, err = _run(["taskkill", "/IM", args.name, "/F", "/T"], timeout=60)
    else:
        code, out, err = _run(["tasklist", "/FO", "CSV", "/NH"], timeout=60)
        if code == 0:
            for line in out.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2 and parts[1] == str(args.pid) and parts[0].lower() in PROTECTED_IMAGES:
                    _fail(f"PID {args.pid} is {parts[0]}: protected runtime (Aali's "
                          "training/agent processes) - refusing")
        code, out, err = _run(["taskkill", "/PID", str(args.pid), "/F", "/T"], timeout=60)
    if code != 0:
        _fail("taskkill failed: " + (err or out or "")[-300:])
    _ok({"killed": args.pid or args.name, "output": (out or "").strip()[:200]})


def cmd_system_info(args: argparse.Namespace) -> None:
    """Read-only snapshot: GPU, RAM, disk, battery, uptime."""
    info: dict = {}
    code, out, _ = _run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                         "--format=csv,noheader"], timeout=30)
    if code == 0 and out.strip():
        parts = [p.strip() for p in out.strip().splitlines()[0].split(",")]
        info["gpu"] = dict(zip(["name", "vram_used", "vram_total", "utilization", "temp_c"], parts))
    for letter in ("C", "D", "X"):
        drive = Path(f"{letter}:/")
        if drive.exists():
            usage = shutil.disk_usage(str(drive))
            info[f"disk_{letter.lower()}"] = {
                "free_gb": round(usage.free / 2**30, 1),
                "total_gb": round(usage.total / 2**30, 1),
            }
    code, out, _ = _run(["powershell", "-NoProfile", "-Command",
                         "$os=Get-CimInstance Win32_OperatingSystem;"
                         "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1;"
                         "[math]::Round($os.TotalVisibleMemorySize/1MB,1),"
                         "[math]::Round(($os.TotalVisibleMemorySize-$os.FreePhysicalMemory)/1MB,1),"
                         "$cpu.LoadPercentage,"
                         "$cpu.Name"], timeout=60)
    if code == 0 and out.strip():
        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        if len(lines) >= 4:
            info["ram_total_gb"] = float(lines[0])
            info["ram_used_gb"] = float(lines[1])
            info["cpu_load_percent"] = lines[2]
            info["cpu_name"] = lines[3]
    _ok(info)


def main() -> None:
    parser = argparse.ArgumentParser(description="HWK machine operations (open/install/processes/system)")
    sub = parser.add_subparsers(dest="action", required=True)

    p_open = sub.add_parser("open")
    p_open.add_argument("--target", required=True)

    for name, helper in (("install", cmd_install), ("uninstall", cmd_uninstall)):
        p = sub.add_parser(name)
        p.add_argument("--target", required=True)
        p.add_argument("--engine", default="auto", choices=["auto", "winget", "npm"])
        p.add_argument("--silent", action="store_true")
        p.add_argument("--force", action="store_true")
        p.set_defaults(func=helper)

    p_search = sub.add_parser("search_software")
    p_search.add_argument("--target", required=True)
    p_search.add_argument("--engine", default="winget", choices=["winget", "npm"])
    p_search.set_defaults(func=cmd_search_software)

    p_list = sub.add_parser("list_processes")
    p_list.add_argument("--name", default=None)
    p_list.add_argument("--max-results", type=int, default=30)
    p_list.set_defaults(func=cmd_list_processes)

    p_kill = sub.add_parser("kill_process")
    p_kill.add_argument("--pid", type=int, default=None)
    p_kill.add_argument("--name", default=None)
    p_kill.add_argument("--force", action="store_true")
    p_kill.set_defaults(func=cmd_kill_process)

    sub.add_parser("system_info").set_defaults(func=cmd_system_info)

    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args.func(args)


if __name__ == "__main__":
    main()
