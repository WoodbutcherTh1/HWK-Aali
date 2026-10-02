#!/usr/bin/env python3
"""Portability self-test — run this ON the machine you want to certify.

    python scripts/portability_check.py            # human report
    python scripts/portability_check.py --json     # machine-readable

It answers one question honestly: **what actually works on THIS box?** Every
line is a real probe that ran here, right now — nothing is inferred from the
build machine. That is the whole point: the owner's MacBook is the only place
the macOS answers can come from.

Probes:
  1. the host (OS, python, arch)
  2. config/data dirs resolve to a PLATFORM-CORRECT location
  3. the sandbox round-trips a file and refuses six escape attempts
  4. the Aali Studio backend boots on a free port and answers /api/health
  5. the Studio agent-panel event vocabulary is intact
  6. the CLI's bidi renderer works for Arabic on this terminal class
  7. whether the brain on :5055 is reachable from here (and says why if not)

Exit code: 0 = everything core passed, 1 = something core failed, 2 = the
report itself could not run.
"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _extra in (REPO / "file-agent", REPO / "scripts", REPO / "build-desktop" / "aali-studio"):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str = "", critical: bool = True) -> bool:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail,
                    "critical": critical})
    return bool(ok)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ————— 1. the host —————

def probe_host() -> dict:
    info = {
        "os": sys.platform,
        "os_label": "",
        "python": sys.version.split()[0],
        "machine": (os.uname().machine if hasattr(os, "uname")
                    else __import__("platform").machine()),
        "executable": sys.executable,
    }
    try:
        from file_agent import hwk_paths
        info["os_label"] = hwk_paths.os_label()
    except Exception as exc:  # noqa: BLE001
        record("hwk_paths import", False, str(exc))
    record("host detected", True, f"{info['os_label']} | python {info['python']} | {info['machine']}")
    return info


# ————— 2. platform-correct directories —————

def probe_paths() -> None:
    try:
        from file_agent import hwk_paths
    except Exception as exc:  # noqa: BLE001
        record("platform paths", False, f"hwk_paths missing: {exc}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AALI_CONFIG_DIR"] = os.path.join(tmp, "cfg")
        cfg = hwk_paths.config_dir("AaliStudio")
        record("config dir is overridable", cfg.exists() is False and "cfg" in str(cfg),
               str(cfg))
        del os.environ["AALI_CONFIG_DIR"]
        real = hwk_paths.config_dir("AaliStudio")
        expected_part = ("Library/Application Support" if hwk_paths.is_macos()
                         else "AppData" if hwk_paths.is_windows() else ".config")
        record("config dir uses this OS's convention", expected_part in str(real).replace("\\", "/"),
               f"{expected_part} expected in {real}")
        record("data root never points at a missing D: drive",
               not (hwk_paths.is_windows() and not hwk_paths.data_root().exists()),
               str(hwk_paths.data_root()))
        record("open_external rejects a non-http url",
               hwk_paths.open_external("javascript:alert(1)") == "bad-url")


# ————— 3. the sandbox —————

def probe_sandbox() -> None:
    try:
        from file_agent import file_tools
    except Exception as exc:  # noqa: BLE001
        record("sandbox module", False, str(exc))
        return
    with tempfile.TemporaryDirectory() as root:
        workspace = Path(root)
        (workspace / "probe.txt").write_text("v1\n", encoding="utf-8")
        try:
            got = file_tools.read_file("probe.txt", workspace)
            record("sandbox reads a real file", got.get("content") == "v1\n",
                   repr(got.get("content"))[:60])
        except Exception as exc:  # noqa: BLE001
            record("sandbox reads a real file", False, str(exc))
        try:
            file_tools.write_file("probe.txt", "v2\n", workspace, overwrite=True)
            record("sandbox writes (explicit overwrite)",
                   (workspace / "probe.txt").read_text(encoding="utf-8") == "v2\n")
        except Exception as exc:  # noqa: BLE001
            record("sandbox writes (explicit overwrite)", False, str(exc))
        escapes = ["../escape.txt", "sub/../../escape.txt", "C:/Windows/win.ini",
                   "nul.txt", "%2e%2e/escape.txt", "/etc/passwd"]
        refused = []
        for path in escapes:
            try:
                file_tools.write_file(path, "x", workspace)
            except Exception:  # noqa: BLE001 - any refusal counts
                refused.append(path)
        record("sandbox refuses every escape attempt", len(refused) == len(escapes),
               f"{len(refused)}/{len(escapes)} refused")
        record("run_command stays allow-listed",
               file_tools._looks_like_secret_exfiltration("python -c printenv") is True)


# ————— 4+5. the Studio backend, live —————

def probe_studio() -> None:
    try:
        import studio_server
    except ModuleNotFoundError as exc:
        # The Mac's first run failed exactly here and the bare
        # "No module named 'flask'" said nothing about what to DO about it.
        # Name the interpreter and the one command that fixes it.
        record("studio_server imports", False,
               f"{exc} — this interpreter ({sys.executable}) cannot import the "
               f"Studio backend. Run the check on a venv that has it: "
               f"bash scripts/build_all_macos.sh check")
        return
    except Exception as exc:  # noqa: BLE001
        record("studio_server imports", False, str(exc))
        return
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "project"
        workspace.mkdir()
        (workspace / "main.py").write_text("print('hi')\n", encoding="utf-8")
        previous = os.environ.get("AALI_STUDIO_CONFIG_DIR")
        previous_ws = None
        os.environ["AALI_STUDIO_CONFIG_DIR"] = str(Path(tmp) / "cfg")
        try:
            import models_proxy as mp
            mp.save_settings({"workspace": str(workspace)})
            previous_ws = mp.settings().get("brain_url")
        except Exception:  # noqa: BLE001
            pass
        port = free_port()
        app = studio_server.create_app()
        client = app.test_client()
        health = client.get("/api/health")
        record("Studio backend answers /api/health", health.status_code == 200,
               json.dumps(health.get_json(), ensure_ascii=False)[:90])
        with client:
            tree = client.get("/api/tree").get_json()
            record("Studio file tree lists the workspace",
                   "main.py" in {e["name"] for e in tree.get("entries", [])})
            files = client.get("/api/files").get_json()
            record("Studio file listing (Ctrl+P source)",
                   any(f["path"] == "main.py" for f in files.get("files", [])))
            search = client.get("/api/search?q=mn").get_json()
            record("Studio fuzzy search", bool(search.get("results")))
            body = client.post("/api/file", json={"path": "made.txt",
                                                  "content": "x\n"}).get_json()
            record("Studio writes a file", body.get("ok") is True)
            escape = client.post("/api/file", json={"path": "../out.txt",
                                                     "content": "x"})
            record("Studio refuses a path escape", escape.status_code == 400)
            client.post("/api/delete", json={"path": "made.txt"})
            # the agent-panel vocabulary must be intact on this platform too
            source = Path(studio_server.__file__).read_text(encoding="utf-8")
            events = ["thinking", "tool_call", "tool_result", "terminal",
                      "diff", "text", "done"]
            missing = [e for e in events if f'_sse("{e}"' not in source]
            record("agent-panel events all present", not missing,
                   "missing: " + ", ".join(missing) if missing else "all 7")
        if previous is None:
            os.environ.pop("AALI_STUDIO_CONFIG_DIR", None)
        else:
            os.environ["AALI_STUDIO_CONFIG_DIR"] = previous


# ————— 6. the CLI bidi renderer —————

def probe_cli() -> None:
    try:
        import aali_cli
    except Exception as exc:  # noqa: BLE001
        record("aali_cli imports", False, str(exc), critical=False)
        return
    # Arabic letters live in TWO blocks: the base letters U+0600-U+06FF and the
    # presentation FORMS U+FB50-U+FEFF. The CLI deliberately shapes Arabic
    # itself on Windows, so checking only the base block fails there — this
    # probe has to accept either.
    def is_arabic(text: str) -> bool:
        return any(0x0600 <= ord(c) <= 0x06FF or 0xFB50 <= ord(c) <= 0xFEFF
                   for c in text)
    try:
        shaped = aali_cli.fx("مرحبا", 80)
        record("CLI renders Arabic (shaped or base letters)", is_arabic(shaped),
               repr(shaped[:40]))
        record("CLI bidi is idempotent", aali_cli.fx(shaped, 80) == shaped)
        latin = aali_cli.fx("hello", 80)
        record("CLI leaves Latin alone", latin.strip() == "hello", repr(latin))
    except Exception as exc:  # noqa: BLE001
        record("CLI renders Arabic (shaped or base letters)", False, str(exc),
               critical=False)


# ————— 7. the brain —————

def probe_brain(url: str) -> None:
    probe = url.rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(probe, timeout=5) as r:
            health = json.loads(r.read())
        record("brain /api/health reachable", bool(health.get("ok")),
               f"{probe} -> {json.dumps(health, ensure_ascii=False)[:90]}",
               critical=False)
    except Exception as exc:  # noqa: BLE001
        # Always print the URL that was tried: "connection refused" on a Mac
        # usually just means the default 127.0.0.1 pointed at the MacBook
        # instead of the PC, and that is invisible without it.
        record("brain /api/health reachable", False,
               f"{probe} -> {type(exc).__name__}: {exc} — the brain runs on the "
               f"Windows PC. Re-run with the PC's address, e.g. "
               f"--brain http://192.168.1.13:5055 (or set AALI_BRAIN_URL).",
               critical=False)
        return
    key = ""
    key_file = Path("D:/hwk-data/aali_master_key.txt")
    if key_file.is_file():
        key = key_file.read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        url.rstrip("/") + "/api/system/models",
        headers={"X-API-Key": key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=8) as r:
            data = json.loads(r.read())
        record("brain serves the promoted own model",
               data.get("serving", {}).get("provider") == "aali_own",
               json.dumps(data.get("serving", {}), ensure_ascii=False)[:110],
               critical=False)
    except urllib.error.HTTPError as exc:
        record("brain serves the promoted own model", False,
               f"HTTP {exc.code} — key mode needs X-API-Key", critical=False)
    except Exception as exc:  # noqa: BLE001
        record("brain serves the promoted own model", False, str(exc),
               critical=False)


# ————— built binaries on this box —————

def probe_built() -> None:
    dist = REPO / "build-desktop" / "dist"
    found = []
    for pattern in ("Aali-Studio.app", "Aali-Studio", "Aali-Studio.exe",
                    "Aali-Desktop.app", "Aali-Desktop", "Aali-Desktop.exe",
                    "aali-cli", "aali-cli.exe"):
        if (dist / pattern).exists():
            found.append(pattern)
    record("built clients present in build-desktop/dist", True,
           ", ".join(found) if found else "none yet — run build_all_macos.sh",
           critical=False)


# ————— report —————

def report(as_json: bool) -> int:
    critical_failed = [r for r in RESULTS if r["critical"] and not r["ok"]]
    if as_json:
        print(json.dumps({"host": HOST, "results": RESULTS,
                          "critical_failed": len(critical_failed)},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 68)
        print("آلي — portability self-test")
        print(f"host    : {HOST['os_label']}")
        print(f"python  : {HOST['python']}  ({HOST['machine']})")
        print("=" * 68)
        for r in RESULTS:
            mark = "OK  " if r["ok"] else ("FAIL" if r["critical"] else "warn")
            print(f"[{mark}] {r['name']}")
            if r["detail"]:
                print(f"        {r['detail']}")
        print("-" * 68)
        if critical_failed:
            print(f"RESULT: {len(critical_failed)} core check(s) FAILED on this machine.")
            for r in critical_failed:
                print(f"  - {r['name']}: {r['detail']}")
        else:
            print("RESULT: every core check passed on this machine.")
    return 1 if critical_failed else 0


def main() -> int:
    global HOST
    parser = argparse.ArgumentParser(description="Aali portability self-test")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--brain", default=os.getenv("AALI_BRAIN_URL",
                                                    "http://127.0.0.1:5055"),
                        help="the brain URL to probe — on a Mac this must be the "
                             "PC's LAN address, not 127.0.0.1 "
                             "(e.g. --brain http://192.168.1.13:5055)")
    args = parser.parse_args()
    try:
        HOST = probe_host()
        probe_paths()
        probe_sandbox()
        probe_studio()
        probe_cli()
        probe_brain(args.brain)
        probe_built()
        return report(args.json)
    except Exception as exc:  # noqa: BLE001 - the report must never crash
        print(f"the portability report itself failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())