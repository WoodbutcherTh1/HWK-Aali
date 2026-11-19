"""Overnight aftercare for the soup_3b graduation (launched 2026-10-06 20:27).

The graduation launch stopped the brain watchdog and killed :20129 so the
pipeline could own the card. Whatever the verdict says, the MORNING must find
a working brain in the right state - this script waits for the verdict and
puts that state in place. It NEVER kills anything and NEVER touches the
trainer; its only spawn is a restore serve when the pipeline left :20129 down.

Verdict contract (soup_pipeline.write_verdict, exact wordings):
- "PROMOTE - ..."  : the pipeline left the tuned-3B serving on :20129 AND
  rewrote promoted.json. Action: relaunch the brain watchdog (the launch
  stopped it) and verify the served model id answers.
- "NO-GO ..."/"INCOMPLETE ...": the pipeline stopped its server (or never
  got one up). Action: serve checkpoint-3933 from the pre-launch backup
  (tuned.checkpoint3933.promoted.bak), verify its id, rewrite promoted.json
  to match, relaunch the brain watchdog.
- Pipeline process gone with NO verdict after the deadline: treated as
  INCOMPLETE (the GPU-wait exit path writes no verdict) - restore.

Kill switch: AALI_3B_AFTERCARE_OFF=1. Single run per pidlock; launch detached:
    .venv/Scripts/python.exe scripts/launch_detached.py --log soup3b_aftercare -- \
        .venv/Scripts/python.exe scripts/soup3b_aftercare.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import soup_pipeline as sp  # noqa: E402  (REPORTS, SOUP_EXE, PORT - one source of truth)

PIPELINE_LOG = Path("D:/hwk-data/soup_3b.log")
BACKUP_ADAPTER = Path("D:/hwk-data/soup/tuned.checkpoint3933.promoted.bak/checkpoint-3933")
PROMOTED_JSON = Path("D:/hwk-data/soup/promoted.json")
WATCHDOG_LOGNAME = "aali_brain_watchdog"
POLL_SECONDS = 300
DEADLINE_SECONDS = 12 * 3600
BASE_1_5B = "D:/hwk-models/Qwen2.5-1.5B-Instruct"
DATA = Path("D:/hwk-data")

_log_path = DATA / "soup3b_aftercare.log"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with _log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def parse_verdict(report_text: str) -> str | None:
    """Extract PROMOTE / NO-GO / INCOMPLETE from the report, else None."""
    if not report_text:
        return None
    match = re.search(r"Verdict:\s*(PROMOTE|NO-GO|INCOMPLETE)", report_text)
    return match.group(1) if match else None


def served_model_id(port: int = sp.PORT, timeout: float = 8.0) -> str | None:
    """The model id the endpoint serves (wrong-model lesson: verify, never assume)."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/models", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        entries = data.get("data") or []
        return str(entries[0].get("id")) if entries else None
    except Exception:
        return None


def pipeline_alive() -> bool:
    """True while the pipeline process (shim or real) is still running."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30)
        if "soup_pipeline" not in out.stdout:
            # tasklist does not show cmdline; fall back to wmic-less CIM.
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process | Where-Object "
                 "{ $_.CommandLine -match 'soup_pipeline.py' } | Measure-Object).Count"],
                capture_output=True, text=True, timeout=45)
            return ps.stdout.strip().rstrip() not in ("", "0")
        return True
    except Exception:
        return True  # unknown -> keep waiting; the deadline decides


def relaunch_watchdog() -> bool:
    """Relaunch the brain watchdog exactly the way aali_autostart.bat does."""
    cmd = [sys.executable, str(REPO / "scripts" / "launch_detached.py"),
           "--log", WATCHDOG_LOGNAME,
           "--", sys.executable, str(REPO / "scripts" / "aali_brain_watchdog.py")]
    try:
        proc = subprocess.Popen(cmd, cwd=str(REPO),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"brain watchdog relaunch requested (launcher pid {proc.pid})")
        return True
    except OSError as exc:
        log(f"watchdog relaunch FAILED: {exc}")
        return False


def rewrite_promoted_json_for_backup() -> None:
    """Point promoted.json at the restored checkpoint-3933 (field-compatible)."""
    old = {}
    try:
        old = json.loads(PROMOTED_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    payload = {
        "adapter_dir": str(BACKUP_ADAPTER),
        "base_model": old.get("base_model") or BASE_1_5B,
        "base_url": f"http://127.0.0.1:{sp.PORT}/v1",
        "port": sp.PORT,
        "baseline_score": old.get("baseline_score", 1),
        "tuned_score": old.get("tuned_score", 3),
        "promoted_at": old.get("promoted_at", "restored-by-aftercare"),
        "restored_by": "soup3b_aftercare",
    }
    PROMOTED_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"promoted.json rewritten -> {BACKUP_ADAPTER}")


def wait_for_serve(expect: str, timeout_s: int = 900) -> bool:
    """Wait for :20129 to serve a model whose id contains `expect`."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        model_id = served_model_id()
        if model_id and expect in model_id:
            log(f"endpoint verified: serving '{model_id}'")
            return True
        time.sleep(20)
    log(f"endpoint never served '{expect}' (last id: {served_model_id()!r})")
    return False


def handle_promote() -> int:
    model_id = served_model_id()
    log(f"PROMOTE branch: pipeline left '{model_id}' serving on :{sp.PORT}")
    try:
        payload = json.loads(PROMOTED_JSON.read_text(encoding="utf-8"))
        log(f"promoted.json now points at: {payload.get('adapter_dir')}")
    except (OSError, json.JSONDecodeError) as exc:
        log(f"promoted.json unreadable ({exc}) - watchdog would refuse; restoring instead")
        return handle_restore()
    relaunch_watchdog()
    log("morning state: tuned-3B live on :20129, watchdog armed")
    return 0


def handle_restore() -> int:
    if served_model_id() is None:
        log(f"no server on :{sp.PORT} - serving the backup adapter")
        cmd = [sys.executable, str(REPO / "scripts" / "launch_detached.py"),
               "--log", "brain_restore_night",
               "--", str(sp.SOUP_EXE), "serve", "--model", str(BACKUP_ADAPTER),
               "--port", str(sp.PORT)]
        subprocess.Popen(cmd, cwd=str(REPO),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_for_serve("checkpoint-3933"):
        log("RESTORE NOT VERIFIED - morning human must serve "
            f"{BACKUP_ADAPTER} by hand")
        return 1
    rewrite_promoted_json_for_backup()
    relaunch_watchdog()
    log("morning state: checkpoint-3933 restored on :20129, watchdog armed")
    return 0


def main() -> int:
    if os.getenv("AALI_3B_AFTERCARE_OFF") == "1":
        log("kill switch AALI_3B_AFTERCARE_OFF=1 - exiting")
        return 0
    log("aftercare armed: watching for the soup_3b verdict")
    started = time.time()
    verdict: str | None = None
    while time.time() - started < DEADLINE_SECONDS:
        try:
            verdict = parse_verdict(
                (sp.REPORTS / "resft_pipeline_report.md").read_text(encoding="utf-8"))
        except OSError:
            verdict = None
        if verdict:
            break
        if not pipeline_alive() and time.time() - started > 1800:
            log("pipeline process is gone and no verdict - treating as INCOMPLETE")
            verdict = "INCOMPLETE"
            break
        time.sleep(POLL_SECONDS)

    if not verdict:
        log(f"deadline hit with no verdict - restoring checkpoint-3933 as the safe state")
        return handle_restore()
    log(f"verdict detected: {verdict}")
    if verdict == "PROMOTE":
        return handle_promote()
    return handle_restore()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
