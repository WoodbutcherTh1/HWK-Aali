"""Salvage finisher for a pipeline run whose code predates the tuned-exam fix.

The run launched at 2026-09-12 12:48 imported the OLD soup_pipeline (the
09-09 rewrite silently dropped `tuned = run_exam(...)`), so after training
it serves the tuned adapter, health-checks it, and then dies with a
NameError at write_verdict(baseline, tuned) - leaving the tuned-adapter
server leaked on :20129 and NO verdict.

This finisher (launched detached, polling) closes the gap:
  1. wait until the trainer is really done (shared GPU gate, log idle)
  2. use the pipeline's leaked server on :20129 if it answers, else serve
     the newest adapter itself
  3. run the SAME tuned exam the pipeline would have run (sp.run_exam)
  4. write the SAME verdict (sp.write_verdict) - PROMOTE keeps the server
     up as the live brain; anything else stops it

Idempotent: if a fresh verdict already exists, it exits without touching
anything. The fixed soup_pipeline makes this helper unnecessary for FUTURE
runs; it exists to save THIS run's 2.5 hours of training.

Usage:
  python scripts/finish_pipeline.py            # poll and salvage
  python scripts/finish_pipeline.py --once     # one attempt, then exit
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import soup_pipeline as sp  # noqa: E402
import wait_gpu_free as gate  # noqa: E402

REPORTS = Path("D:/hwk-data/soup")
REPORT = REPORTS / "resft_pipeline_report.md"
BASELINE_REPORT = REPORTS / "soup_exam_report_baseline.json"
PORT = sp.PORT
LOG_STAMP = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731


def log(message: str) -> None:
    print(f"[{LOG_STAMP()}] {message}", flush=True)


def verdict_is_fresh(max_age_s: float = 6 * 3600) -> bool:
    try:
        age = time.time() - REPORT.stat().st_mtime
    except OSError:
        return False
    if age > max_age_s:
        return False
    text = REPORT.read_text(encoding="utf-8", errors="replace")
    return ("Verdict: PROMOTE" in text or "Verdict: NO-GO" in text)


LOCK_PATH = Path(os.environ.get("TEMP", "/tmp")) / "hwk_soup_pipeline.lock"


def old_pipeline_alive() -> bool:
    """The crashed-run pipeline holds the singleton lock with its PID; while
    that PID lives, the old code may still be mid-stage - never race it."""
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return False
    return pid > 0 and sp.psutil is not None and sp.psutil.pid_exists(pid)


def trainer_done() -> bool:
    """No python compute on the GPU, the live train log idle 15+ min, and
    the old pipeline process itself gone (same bar the pipeline uses)."""
    ok, _ = gate.card_is_safe(require_training_log_idle=False)
    if not ok:
        return False
    try:
        live = Path("D:/hwk-data/soup_train_live.log").stat().st_mtime
        if (time.time() - live) / 60 < sp.IDLE_MINUTES:
            return False
    except OSError:
        pass
    return not old_pipeline_alive()


def _kill_port_owner(port: int) -> None:
    """House rule: never kill by image name - look up the exact PID owning
    the port first, then kill that PID only."""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            pid = line.split()[-1]
            if pid.isdigit():
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True)
            return


def endpoint_up(port: int, timeout_s: int = 900) -> bool:
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/v1/models"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(10)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Salvage a crashed pipeline tail")
    parser.add_argument("--once", action="store_true",
                        help="one salvage attempt, no polling loop")
    parser.add_argument("--poll-s", type=int, default=300)
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    log("finish_pipeline on watch (salvaging the 09-09 dropped tuned exam)")
    while True:
        if verdict_is_fresh():
            log("a fresh verdict already exists - nothing to salvage")
            return 0
        if trainer_done():
            break
        if args.once:
            log("trainer not done yet (--once) - exiting without action")
            return 0
        time.sleep(args.poll_s)

    log("trainer done - salvaging the pipeline tail")
    if verdict_is_fresh():  # re-check: the pipeline may have just finished
        log("fresh verdict appeared - nothing to salvage")
        return 0

    baseline = None
    try:
        baseline = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
        log(f"baseline loaded: {baseline.get('score')}")
    except (OSError, ValueError):
        log("baseline report missing/unreadable - verdict will be INCOMPLETE")

    # Reuse the pipeline's leaked tuned-adapter server when it answers;
    # otherwise serve the newest adapter ourselves (same port, same way).
    if endpoint_up(PORT, timeout_s=60):
        log(f"tuned-adapter server already up on :{PORT} (leaked by the crash)")
        server = None
    else:
        adapter = sp._adapter_dir()
        log(f"serving tuned adapter on :{PORT} ({adapter})")
        try:
            server = subprocess.Popen(
                [str(sp.SOUP_EXE), "serve", "--model", str(adapter),
                 "--port", str(PORT)],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log(f"could not serve the tuned adapter ({exc})")
            sp.write_verdict(baseline, None, "salvage: adapter server failed to start")
            return 1
        if not endpoint_up(PORT, timeout_s=900):
            log("adapter server never became ready - refusing to grade a dead endpoint")
            server.terminate()
            sp.write_verdict(baseline, None, "salvage: adapter server never became ready")
            return 1

    tuned = sp.run_exam("tuned", sp._model_for_adapter())
    if tuned is None:
        sp.write_verdict(baseline, None, "salvage: tuned exam failed")
        if server is not None:
            server.terminate()
        return 1
    report_text = sp.write_verdict(baseline, tuned)
    log("verdict written")
    if "Verdict: PROMOTE" in report_text:
        log(f"PROMOTED - adapter stays serving on :{PORT}")
    else:
        log("no promotion - stopping the tuned-adapter server")
        if server is not None:
            server.terminate()
        else:
            # the leaked server must not linger on a NO-GO: kill exactly the
            # PID owning :20129 (house rule - never kill by image name)
            _kill_port_owner(PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
