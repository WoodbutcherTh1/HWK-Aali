"""Restore the promoted checkpoint-2900 adapter as Aali's live brain.

Use when a later graduation run ends NO-GO (or the promoted adapter must
come back for any reason): stop whatever serves :20129 (by PID - the house
rule is never kill by image name), serve the backed-up adapter the same way
the pipeline does, verify the endpoint actually serves the right model, and
rewrite promoted.json. Optional --exam re-grades the restored adapter on the
26-case exam (expect 4/26) so the restore is proven, not assumed.

The backup this restores from was made 2026-09-12 before the sft_v3 launch:
    D:/hwk-data/soup/tuned.checkpoint2900.promoted.bak/checkpoint-2900

Run (detached not needed - it exits on its own):
    .venv/Scripts/python.exe scripts/restore_checkpoint2900.py [--exam]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import soup_pipeline as sp  # noqa: E402

# Pre-launch backup of the promoted adapter. Overridable per-run (2026-09-13):
# each graduation replaces a different promoted checkpoint (3954 replaced
# 3843; the next run replaces 3954), and watch_verdict_restore.py passes
# AALI_RESTORE_BACKUP so both scripts always agree on the same backup.
# --backup CLI flag wins over the env var, which wins over the default.
BACKUP_ADAPTER = Path(os.environ.get(
    "AALI_RESTORE_BACKUP",
    "D:/hwk-data/soup/tuned.checkpoint2900.promoted.bak/checkpoint-2900"))
PROMOTED_JSON = Path("D:/hwk-data/soup/promoted.json")
BASELINE_SCORE = 1
EXPECTED_TUNED_SCORE = 4  # the 2026-09-12 salvage exam result


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def kill_port_owner(port: int) -> bool:
    """Kill exactly the PID listening on the port (finish_pipeline house rule)."""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
            pids.add(int(parts[4]))
    if not pids:
        log(f":{port} already free")
        return True
    for pid in pids:
        log(f"killing pid {pid} (owner of :{port})")
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    time.sleep(3)
    return True


def endpoint_up(url: str, timeout_s: int) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(5)
    return False


def served_model(port: int) -> str | None:
    """The model id the endpoint claims - never trust a port that serves
    the wrong brain (the wrong-model lesson from the salvage hardening)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        rows = data.get("data") or []
        return str(rows[0].get("id")) if rows else None
    except (OSError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backup", default="",
                        help="adapter dir to restore (default: AALI_RESTORE_BACKUP "
                             "env, then the checkpoint-2900 backup)")
    parser.add_argument("--exam", action="store_true",
                        help="re-grade the restored adapter on the 26-case exam")
    args = parser.parse_args()

    # --backup flag wins over the env-derived default (watch_verdict_restore
    # passes AALI_RESTORE_BACKUP so both scripts agree on the same backup).
    global BACKUP_ADAPTER
    if args.backup:
        BACKUP_ADAPTER = Path(args.backup)

    if not BACKUP_ADAPTER.exists():
        log(f"RESTORE FAILED: backup adapter missing: {BACKUP_ADAPTER}")
        return 1
    log(f"restoring {BACKUP_ADAPTER}")

    kill_port_owner(sp.PORT)

    log(f"starting Soup server on :{sp.PORT} with the backup adapter")
    server = subprocess.Popen(
        [str(sp.SOUP_EXE), "serve", "--model", str(BACKUP_ADAPTER),
         "--port", str(sp.PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        if not endpoint_up(f"http://127.0.0.1:{sp.PORT}/v1/models", timeout_s=900):
            log("RESTORE FAILED: server never became ready")
            server.terminate()
            return 1
        model_id = served_model(sp.PORT)
        log(f"endpoint up - serving model id: {model_id!r}")
        backup_name = BACKUP_ADAPTER.name  # e.g. checkpoint-2900 / checkpoint-3954
        if model_id and backup_name not in model_id:
            log(f"RESTORE FAILED: endpoint is NOT serving {backup_name}")
            server.terminate()
            kill_port_owner(sp.PORT)
            return 1

        if args.exam:
            tuned = sp.run_exam(f"restored-{backup_name}", sp._model_for_adapter())
            if tuned is None:
                log("RESTORE WARNING: exam failed - adapter stays serving anyway")
            else:
                score = int(tuned.get("score", -1))
                log(f"restored adapter exam score: {score}/26 "
                    f"(expected ~{EXPECTED_TUNED_SCORE})")
    except KeyboardInterrupt:
        log("interrupted - leaving the restored server running")

    promoted = {
        "adapter_dir": str(BACKUP_ADAPTER),
        "base_model": "D:/hwk-models/Qwen2.5-1.5B-Instruct",
        "base_url": f"http://127.0.0.1:{sp.PORT}/v1",
        "port": sp.PORT,
        "baseline_score": BASELINE_SCORE,
        "tuned_score": EXPECTED_TUNED_SCORE,
        "promoted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": f"restored from {BACKUP_ADAPTER}",
    }
    PROMOTED_JSON.write_text(json.dumps(promoted, indent=2) + "\n", encoding="utf-8")
    log(f"promoted.json updated - {backup_name} is the live brain on :{sp.PORT}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
