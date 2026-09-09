"""Night caretaker: the autonomous operator while the owner sleeps.

Runs for up to 7 hours, checking every 10 minutes, and:

1. WATCHES Phase A training (progress + anomalies in training.log).
2. AUTO-HEALS the mentor lab: if the lab process died before finishing its
   30 tasks, relaunches it (it is resumable by design).
3. When the GPU frees (training done or crashed):
   - audits + prunes the mentor lab, rebuilds sft_v2 + soup export
   - runs the soup pipeline if it is not already running/finished
     (teacher serve -> 26-case baseline exam -> re-SFT -> tuned exam -> verdict)
4. CHAINS the 4096 context extension afterwards IF the GPU is still free
   (equivalent of scripts/after_phaseA.bat) - the last big stage.
5. Writes a morning report the owner reads on waking:
   D:/hwk-data/MORNING_REPORT.md

Safety: never touches a running python GPU process; every launch is via
its own logged subprocess; the report records every decision with a reason.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# Shared GPU gate (scripts/wait_gpu_free.py) - one source of truth for
# "is the 8GB card free", used by every launcher on this machine.
import wait_gpu_free as gate  # noqa: E402

TRAINING_LOG = Path("D:/hwk-data/training.log")
CARETAKER_LOG = Path("D:/hwk-data/caretaker.log")
MORNING_REPORT = Path("D:/hwk-data/MORNING_REPORT.md")
LAB_LOG = Path("D:/hwk-data/mentor_lab_run.log")
PIPELINE_LOG = Path("D:/hwk-data/soup_pipeline.log")
REPORTS = Path("D:/hwk-data/soup")
CHECK_INTERVAL = 600          # 10 minutes
MAX_RUNTIME = 7 * 3600
IDLE_MINUTES = 15

DECISIONS: list[str] = []


def log(message: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        with CARETAKER_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def decide(decision: str) -> None:
    DECISIONS.append(f"{datetime.now(timezone.utc).strftime('%H:%M')} - {decision}")
    log(f"DECISION: {decision}")


def training_state() -> dict:
    """Latest step/eval + whether the trainer process is alive."""
    state = {"step": 0, "eval": None, "log_idle_min": None, "process_alive": False}
    try:
        text = TRAINING_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(text):
            match = re.search(r"step=(\d+).*?eval_loss=([\d.]+)", line)
            if match:
                state["step"] = int(match.group(1))
                state["eval"] = float(match.group(2))
                break
        state["log_idle_min"] = (time.time() - TRAINING_LOG.stat().st_mtime) / 60
    except OSError:
        pass
    # None (query failed) reads as not-alive here for display, but the
    # safety decision in gpu_really_free() still blocks via card_is_safe's
    # fail-closed process check.
    state["process_alive"] = bool(gate.python_compute_pids())
    return state


def process_running(match: str) -> int | None:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
             "Select-Object ProcessId, CommandLine | ConvertTo-Json"],
            capture_output=True, text=True, timeout=60,
        ).stdout
        payload = json.loads(out) if out.strip() else []
        if isinstance(payload, dict):
            payload = [payload]
        for entry in payload or []:
            command = str(entry.get("CommandLine", ""))
            if match in command and "night_caretaker" not in command:
                return int(entry.get("ProcessId"))
    except (OSError, ValueError):
        return None
    return None


def launch(script: str, args: str = "", log_name: str = "caretaker_child.log") -> bool:
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             f"Start-Process -WindowStyle Hidden -FilePath "
             f"'{ROOT / '.venv' / 'Scripts' / 'python.exe'}' "
             f"-ArgumentList 'scripts/{script} {args}' "
             f"-WorkingDirectory '{ROOT}' "
             f"-RedirectStandardOutput 'D:/hwk-data/{log_name}' "
             f"-RedirectStandardError 'D:/hwk-data/{log_name}.err'"],
        )
        return True
    except OSError as exc:
        log(f"launch failed for {script}: {exc}")
        return False


def gpu_really_free() -> bool:
    """Log idle AND no python/soup compute process on the GPU.

    Lesson (2026-09-08 night): a running soup train/serve does not match the
    train_scratch process check and its output is captured (not streamed to
    training.log), so the log-idle check alone looked 'free' while the GPU was
    fully busy - the caretaker then stacked jobs and OOM'd the pipeline. The
    nvidia-smi compute-app list is the source of truth - delegated to the
    shared gate (scripts/wait_gpu_free.py), same as soup_pipeline.
    """
    ok, reason = gate.card_is_safe()
    if not ok:
        log(f"GPU not free: {reason}")
        return False
    state = training_state()
    idle = state["log_idle_min"] is None or state["log_idle_min"] >= IDLE_MINUTES
    return idle and not state["process_alive"]


def write_morning_report() -> None:
    training = training_state()
    lab_done = _lab_count()
    verdict = ""
    verdict_path = REPORTS / "resft_pipeline_report.md"
    if verdict_path.exists():
        verdict = verdict_path.read_text(encoding="utf-8", errors="replace")
    lines = [
        "# صباح الخير — Morning report (night caretaker)",
        f"_generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## Phase A",
        f"- latest step: {training['step']} / 90,000"
        f" (eval_loss: {training['eval']})",
        f"- trainer process alive: {training['process_alive']}"
        f" (log idle {training['log_idle_min'] and round(training['log_idle_min'], 1)} min)",
        "",
        "## Mentor lab",
        f"- episodes captured: {lab_done}/30",
        "",
        "## Re-SFT pipeline",
        "- " + ("FINISHED - see verdict below" if verdict
                else "waiting/running - check D:/hwk-data/soup_pipeline.log"),
        "",
        verdict or "",
        "## Decisions the caretaker made tonight",
        *(f"- {decision}" for decision in DECISIONS),
    ]
    MORNING_REPORT.write_text("\n".join(lines), encoding="utf-8")
    log("morning report written")


def _lab_count() -> int:
    lab_file = Path("D:/hwk-data/mentor/omniroute_lab.jsonl")
    if not lab_file.exists():
        return 0
    return sum(1 for line in lab_file.read_text(encoding="utf-8", errors="replace").splitlines()
               if line.strip())


def main() -> int:
    started = time.time()
    log("=== night caretaker on duty ===")
    # Owner request (2026-09-08): snapshot the Obsidian brain vault to X:
    # before the night's GPU work, so every training run starts from a
    # recorded state of the project.
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_brain_vault.py"),
             "--out", "X:/hwk-backups/aali-brain-vault"],
            capture_output=True, text=True, timeout=120,
        )
        decide("brain vault exported to X:/hwk-backups/aali-brain-vault")
    except Exception as exc:  # noqa: BLE001
        log(f"vault export failed (non-fatal): {exc}")
    pipeline_launched = False
    extension_launched = False

    while time.time() - started < MAX_RUNTIME:
        state = training_state()
        lab_count = _lab_count()

        # 1) lab auto-heal
        if lab_count < 30 and process_running("omniroute_mentor_lab") is None:
            decide(f"mentor lab dead at {lab_count}/30 - relaunching")
            launch("omniroute_mentor_lab.py", "", "mentor_lab_run.log")
            time.sleep(60)

        # 2) training finished?
        if gpu_really_free():
            decide("GPU free - training window over")
            # 2a) audit + rebuild data with everything captured so far
            audit = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "audit_mentor_lab.py")],
                capture_output=True, text=True, timeout=1800,
            )
            decide(f"lab audit done: {audit.stdout.strip().splitlines()[-1:] or 'see log'}")
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "build_aali_sft_v2.py")],
                capture_output=True, text=True, timeout=1800,
            )
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "soup_export_sft.py")],
                capture_output=True, text=True, timeout=1800,
            )
            decide("sft_v2 + soup export rebuilt with lab + arabic data")

            # 2b) re-SFT pipeline (if not already handled). A verdict newer
            # than tonight's start means the pipeline already ran — skip it.
            if not pipeline_launched and process_running("soup_pipeline") is None:
                verdict = REPORTS / "resft_pipeline_report.md"
                fresh = verdict.exists() and verdict.stat().st_mtime > started
                if fresh:
                    decide("soup pipeline already produced a verdict tonight - skipping")
                else:
                    decide("launching soup pipeline (exam baseline -> re-SFT -> tuned exam)")
                    pipeline_launched = launch("soup_pipeline.py", "--no-wait",
                                               "soup_pipeline_run.log")
                    # give it time; poll for its verdict up to 3.5 hours
                    for _ in range(21):
                        time.sleep(CHECK_INTERVAL)
                        if (REPORTS / "resft_pipeline_report.md").exists():
                            decide("pipeline verdict ready")
                            break
                        if process_running("soup_pipeline") is None:
                            decide("pipeline exited (check soup_pipeline_run.log)")
                            break

            # 2b+) Phase A resume: the 90k-step pretrain stopped mid-run
            # (last seen at step 49425/90,000). A stale training.log means
            # Phase A is NOT running - resume it (resumable by design,
            # mirrors to X:). after_phaseA.bat chains the 4096 extension
            # (Phase B) when this run eventually ends.
            training_log = Path("D:/hwk-data/training.log")
            phase_a_idle = True
            try:
                phase_a_idle = (time.time() - training_log.stat().st_mtime) > 1800
            except OSError:
                pass  # no log yet: nothing to protect
            if phase_a_idle and process_running("train_scratch") is None:
                decide("Phase A idle - resuming pretraining (resume_training.bat)")
                subprocess.Popen(
                    [r"C:\Windows\System32\cmd.exe", "/c",
                     str(ROOT / "scripts" / "resume_training.bat")],
                    cwd=str(ROOT),
                )

            # 2c) chain the 4096 context extension (Phase B) via the watcher:
            # after_phaseA.bat waits for training.log idle 15+ min itself, so
            # spawning the WATCHER (not the extension) is collision-free.
            # System32 cmd explicitly: Git-bash PATH made `timeout /t` resolve
            # to GNU timeout and spin the watcher in a fast loop (incident
            # 2026-09-08 21:03).
            if not extension_launched:
                decide("spawning after_phaseA watcher (chains Phase B on Phase A end)")
                extension_launched = True
                subprocess.Popen(
                    [r"C:\Windows\System32\cmd.exe", "/c",
                     str(ROOT / "scripts" / "after_phaseA.bat")],
                    cwd=str(ROOT),
                )
            write_morning_report()
            break

        # still training - status line + keep waiting
        log(f"training alive: step {state['step']}, "
            f"eval {state['eval']}, lab {lab_count}/30")
        time.sleep(CHECK_INTERVAL)

    write_morning_report()
    log("=== night caretaker going to sleep ===")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
