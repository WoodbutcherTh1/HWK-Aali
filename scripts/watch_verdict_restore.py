"""Watch for tonight's graduation verdict, then GUARANTEE a live brain.

Launch detached NOW, before the verdict exists:
    .venv/Scripts/python.exe scripts/launch_detached.py --log brain_watch -- \
        .venv/Scripts/python.exe scripts/watch_verdict_restore.py

Behavior (2026-09-12 owner request: "verify checkpoint-2900 restores cleanly
after the verdict, whichever way it goes"):

1. Poll D:/hwk-data/soup/resft_pipeline_report.md until it exists (60s
   interval, 6h cap), then wait 60s for the pipeline's post-verdict actions
   (promoted.json write / server stop) to settle.
2. PROMOTE  -> verify the NEW adapter is actually serving on :20129 and
   promoted.json points at it. If any check fails, fall back to restoring
   checkpoint-2900 from the pre-launch backup (a proven brain beats no
   brain).
3. NO-GO / INCOMPLETE -> the pipeline stopped its server, leaving :20129
   empty; restore checkpoint-2900 from the backup and PROVE the restore by
   re-grading it on the 26-case exam (expect 4/26).
4. Exit 0 only when a verified brain is live on :20129; exit 1 if every
   recovery attempt failed (human needed).

Log: D:/hwk-data/brain_watch.log (via launch_detached's --log redirect).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

VERDICT = Path("D:/hwk-data/soup/resft_pipeline_report.md")
PROMOTED_JSON = Path("D:/hwk-data/soup/promoted.json")
WATCH_LOG = Path("D:/hwk-data/brain_watch.log")
PORT = 20129
POLL_S = 60
SETTLE_S = 60
TIMEOUT_S = 6 * 3600
# Single source of truth for the pre-launch backup (mirrors
# restore_checkpoint2900.BACKUP_ADAPTER; kept literal here so the watcher
# never imports the heavy soup_pipeline stack just to poll a file).
# Overridable per-run (2026-09-13): each graduation replaces a DIFFERENT
# promoted adapter - checkpoint-3954 replaced 3843, the next run will
# replace 3954 - so the fallback must restore the brain that was actually
# live before the run, not a stale hardcode:
#   AALI_BRAIN_BACKUP=D:/hwk-data/soup/tuned.<tag>/checkpoint-<n>
#   AALI_BRAIN_BACKUP_TAG=<tag>          (the restore_check substring)
#   AALI_RESTORE_BACKUP=<same adapter>   (passed to restore_checkpoint2900)
BACKUP_ADAPTER = Path(os.environ.get(
    "AALI_BRAIN_BACKUP",
    "D:/hwk-data/soup/tuned.checkpoint2900.promoted.bak/checkpoint-2900"))
BACKUP_TAG = os.environ.get("AALI_BRAIN_BACKUP_TAG", "checkpoint2900.promoted.bak")


def log(msg: str) -> None:
    # stdout only - launch_detached already redirects it to brain_watch.log;
    # appending here too doubled every line.
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def models_id(port: int) -> str | None:
    """The model id the endpoint currently claims, or None."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/models", timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8")).get("data") or []
        return str(rows[0].get("id")) if rows else None
    except (OSError, ValueError):
        return None


def endpoint_live(port: int, timeout_s: int = 120) -> bool:
    url = f"http://127.0.0.1:{port}/v1/models"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(5)
    return False


def run_restore(with_exam: bool) -> bool:
    """Restore the pre-launch backup adapter via the dedicated script.
    AALI_RESTORE_BACKUP keeps restore_checkpoint2900.py pointing at the SAME
    backup this watcher verified against (no restore-the-wrong-brain races)."""
    cmd = [sys.executable, str(Path(__file__).with_name("restore_checkpoint2900.py"))]
    if with_exam:
        cmd.append("--exam")
    env = dict(os.environ)
    env["AALI_RESTORE_BACKUP"] = str(BACKUP_ADAPTER)
    log(f"running restore: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=3600,
                          env=env)
    for line in (proc.stdout or "").splitlines()[-15:]:
        log(f"  restore| {line}")
    if proc.returncode != 0:
        log(f"restore script exited {proc.returncode}")
        for line in (proc.stderr or "").splitlines()[-10:]:
            log(f"  restore-err| {line}")
    return proc.returncode == 0


def verify_live_brain(expect_new: bool) -> tuple[bool, str]:
    """endpoint up + model id sane + promoted.json consistent. Returns
    (ok, human-readable detail)."""
    if not endpoint_live(PORT):
        return False, f"no endpoint on :{PORT}"
    model_id = models_id(PORT) or "?"
    try:
        promoted = json.loads(PROMOTED_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, f"promoted.json unreadable (endpoint serves {model_id!r})"
    adapter_dir = str(promoted.get("adapter_dir", ""))
    score = promoted.get("tuned_score", "?")
    if expect_new and BACKUP_TAG in adapter_dir:
        return False, f"promoted.json still points at the BACKUP ({adapter_dir})"
    if not expect_new and BACKUP_TAG not in adapter_dir:
        return False, f"expected the backup restored, got {adapter_dir}"
    return True, (f"brain LIVE on :{PORT} serving {model_id!r} "
                  f"({adapter_dir}, tuned_score={score})")


def main() -> int:
    log("watching for the graduation verdict "
        f"({VERDICT.name}, poll {POLL_S}s, cap {TIMEOUT_S // 3600}h)")
    deadline = time.monotonic() + TIMEOUT_S
    while not VERDICT.exists():
        if time.monotonic() > deadline:
            log("TIMEOUT: no verdict after the cap - pipeline may still be "
                "running; re-run this watcher")
            return 2
        time.sleep(POLL_S)
    log("verdict file detected - letting the pipeline settle "
        f"({SETTLE_S}s) before verification")
    time.sleep(SETTLE_S)

    text = VERDICT.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("**Verdict:") or line.startswith("| tuned"):
            log(f"verdict says: {line.strip('*|')}")

    if "Verdict: PROMOTE" in text:
        log("PROMOTE - verifying the NEW adapter is live on "
            f":{PORT} and promoted.json points at it")
        ok, detail = verify_live_brain(expect_new=True)
        if ok:
            log(f"VERIFIED: {detail}")
            return 0
        log(f"new-brain verification FAILED: {detail}")
        log("falling back to checkpoint-2900 - a proven brain beats no brain")
        if run_restore(with_exam=False) and verify_live_brain(expect_new=False)[0]:
            ok2, detail2 = verify_live_brain(expect_new=False)
            log(f"RESTORED (fallback): {detail2}")
            return 0
        log("FAILED: could not bring any brain live - human needed")
        return 1

    log("NO-GO/INCOMPLETE - the pipeline stopped its server; restoring "
        "checkpoint-2900 from the backup (with exam proof)")
    if run_restore(with_exam=True):
        ok, detail = verify_live_brain(expect_new=False)
        if ok:
            log(f"VERIFIED: {detail}")
            return 0
        log(f"restore ran but verification failed: {detail}")
        return 1
    log("FAILED: restore script could not bring checkpoint-2900 live - "
        "human needed")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
