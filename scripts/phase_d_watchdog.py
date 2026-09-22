#!/usr/bin/env python3
"""Phase D night watchdog - keeps Aali's own-brain pretraining moving unattended.

2026-09-22 night, Buffy. Context: at 20:35 the NVIDIA driver reset (nvlddmkm
Error 153) and silently killed the Phase D trainer while the machine stayed up;
the card sat idle for 4 hours because every manual relaunch died on the
launch_detached WinError 193 bug. The owner is asleep for ~5h - this script
owns the card until morning.

Duties (checked every CHECK_SECONDS):
1. Tokenize the missing corpora (law/medical/know/mideast/mmlu) FIRST, in a
   detached CPU-only process - tokenization never touches the GPU.
2. Watch the active stage's trainer via D:/hwk-data/phase_d_train.log:
   log idle > STALL_MINUTES AND no python compute on the GPU  ->  the trainer
   died (TDR / OOM / anything). Relaunch the SAME stage with --resume; the
   trainer continues from its last checkpoint (save-steps 500 = <=8.5 min loss).
3. Advance stages when the current one finishes (last step >= max-steps and
   GPU free):
     stage 1 (pile,arabic,instruct,orca_math,reasoning -> 3000 steps)  [done]
     stage 2: bootstrap phase-d -> phase-d-code, ADD code (33.3B tokens)
     stage 3: bootstrap phase-d-code -> phase-d-wide, ADD law/medical/...
   Each stage restarts the cosine schedule (fresh optimizer, step 0) exactly
   like bootstrap_sft_state.py intends.
4. Never kills anything by image name; never launches onto a busy card; every
   action is logged to D:/hwk-data/phase_d_watchdog.log.

Usage: .venv/Scripts/python.exe scripts/phase_d_watchdog.py  (detached;
       scripts/launch_detached.py --log phase_d_watchdog -- .venv/Scripts/python.exe scripts/phase_d_watchdog.py)
       --once renders a single check cycle (used by tests).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO = Path(__file__).resolve().parents[1]
PYEXE = REPO / ".venv" / "Scripts" / "python.exe"
DATA = Path("D:/hwk-data")
# Per-stage trainer logs: phase_d_train_stage{N}.log (see stage_log()).
# The old shared phase_d_train.log is archived as phase_d_train.log.stage1-archive.
WATCH_LOG = DATA / "phase_d_watchdog.log"
STATE_FILE = DATA / "phase_d_watchdog_state.json"
TOKENIZE_LOCK = DATA / "tokenize.pidlock"

CHECK_SECONDS = 60
STALL_MINUTES = 12          # log silent + GPU empty for this long = dead trainer
BOOTSTRAP_COOLDOWN = 300    # seconds between retry attempts of the same action

# The stage table. corpora = trainer --corpora value; steps = the stage's own
# step budget (fresh cosine each stage). source/target model dirs chain.
STAGES = [
    {"stage": 1, "output_dir": "D:/hwk-models/phase-d",
     "source_dir": None, "corpora": "pile,arabic,instruct,orca_math,reasoning",
     "max_steps": 3000, "done_marker": None},
    {"stage": 2, "output_dir": "D:/hwk-models/phase-d-code",
     "source_dir": "D:/hwk-models/phase-d",
     "corpora": "pile,arabic,instruct,orca_math,reasoning,code",
     "max_steps": 4000, "done_marker": None},
    {"stage": 3, "output_dir": "D:/hwk-models/phase-d-wide",
     "source_dir": "D:/hwk-models/phase-d-code",
     "corpora": None,  # built at launch time from whatever tokenized by then
     "max_steps": 4000, "done_marker": None},
]
STAGE3_CANDIDATES = ("law", "medical", "know", "mideast", "mmlu")
TOKENIZE_CORPORA = ("law", "medical", "know", "mideast", "mmlu")


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    try:
        with WATCH_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def stage_dir(stage: dict) -> Path:
    return Path(stage["output_dir"])


def tokens_root() -> Path:
    return Path("D:/hwk-data/tokens")


def corpus_needs_tokens(name: str) -> bool:
    """A corpus is only usable by the trainer when it has a manifest -
    shards without one are an interrupted run with unknown boundaries."""
    out = tokens_root() / name
    return not (out / "manifest.json").exists()


def missing_stage3_corpora() -> list[str]:
    """Stage-3 corpora that actually have COMPLETE tokenization on disk
    (manifest present - see corpus_needs_tokens for why shards alone lie)."""
    available = []
    for name in STAGE3_CANDIDATES:
        if not corpus_needs_tokens(name):
            available.append(name)
    return available


def launch_detached(command: list[str], log_base: str) -> int | None:
    """Spawn via the shared launcher (breakaway + no console); return pid."""
    try:
        import launch_detached as launcher
        return launcher.spawn_detached(command, log_base, cwd=REPO)
    except Exception as exc:  # noqa: BLE001 - watchdog must never die
        log(f"launch failed ({log_base}): {exc}")
        return None


def gpu_is_free() -> bool:
    """True only when nvidia-smi answered AND no python compute is listed."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name",
             "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return False   # cannot verify -> assume busy (never double-launch)
        return not any("python" in line.lower()
                       for line in proc.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        return False


def stage_log(stage: dict) -> Path:
    """Per-stage trainer log. (2026-09-22 08:20 lesson: all stages shared one
    log, so stage-2's health check read stage-1's final 'step=3000' lines and
    both misjudged liveness AND double-launched.)"""
    return DATA / f"phase_d_train_stage{stage['stage']}.log"


def log_age_minutes(stage: dict) -> float | None:
    try:
        return (time.time() - stage_log(stage).stat().st_mtime) / 60
    except OSError:
        return None


def last_step(stage: dict) -> int | None:
    """Newest 'step=N' in this stage's training log (None when unreadable)."""
    try:
        lines = stage_log(stage).read_text(encoding="utf-8", errors="replace")\
            .splitlines()[-40:]
    except OSError:
        return None
    for line in reversed(lines):
        if "step=" in line:
            try:
                return int(line.split("step=")[1].split()[0])  # noqa: E501
            except (IndexError, ValueError):
                continue
    return None


def trainer_healthy(stage: dict) -> tuple[bool, str]:
    """(alive, reason) for the ACTIVE stage's trainer."""
    # Launch grace (2026-09-22 race lesson): after a stage advance or restart,
    # the new trainer spends minutes bootstrapping + scanning shards BEFORE its
    # first step= line lands. (08:00 lesson: mtime-based grace is useless here
    # - the trainer writes its banner lines immediately, then may die on the
    # checkpoint load, and append-mode opens skew mtime anyway.) Grace now
    # means: within 25 min of launch AND no step= parsed yet -> give it time.
    # Once a step= exists, the ordinary stall checks below take over.
    state = load_state() or {}
    launched_at = state.get("launched_at")
    if launched_at and state.get("stage") == stage["stage"]:
        try:
            elapsed = time.time() - datetime.fromisoformat(launched_at).timestamp()
            if 0 <= elapsed < 25 * 60 and last_step(stage) is None:
                return True, (f"stage {stage['stage']} starting up "
                              f"({elapsed:.0f}s since launch, "
                              "bootstrap/data scan)")
        except (ValueError, OSError):
            pass
    age = log_age_minutes(stage)
    step = last_step(stage)
    if step is None:
        return False, "training log unreadable/empty"
    if step >= int(stage["max_steps"]):
        return True, f"stage {stage['stage']} COMPLETE (step {step})"
    if age is not None and age > STALL_MINUTES:
        if gpu_is_free():
            return False, f"log idle {age:.0f}min at step {step} + GPU free = dead trainer"
        return True, f"log idle {age:.0f}min but GPU busy - still working (step {step})"
    return True, f"alive at step {step} (last write {age:.1f}min ago)"


def stage3_corpora_arg() -> str | None:
    available = missing_stage3_corpora()
    if not available:
        return None
    base = "pile,arabic,instruct,orca_math,reasoning,code"
    return base + "," + ",".join(available)


def build_stage_command(stage: dict) -> list[str]:
    corpora = stage["corpora"]
    if stage["stage"] == 3:
        corpora = stage3_corpora_arg()
    return [
        str(PYEXE), str(REPO / "train_scratch.py"),
        "--data", "D:/hwk-data/tokens",
        "--corpora", corpora or "pile,arabic",
        "--tokenizer", "D:/hwk-data/tokenizer/hwk_spm.model",
        "--output-dir", stage["output_dir"],
        "--mirror-dir", f"X:/hwk-backups/{stage_dir(stage).name}",
        "--context", "4096", "--d-model", "768", "--heads", "12", "--layers", "12",
        "--batch-size", "1", "--gradient-accumulation", "8", "--grad-checkpoint",
        "--max-steps", str(stage["max_steps"]), "--save-steps", "500",
        "--log-steps", "25", "--learning-rate", "1e-4", "--warmup-steps", "200",
        # dropout 0.1 (2026-09-22 08:00 lesson): the payload config carries
        # dropout=0.1 and --resume compares field-by-field. The 0.0 written
        # here originally made every stage-2 launch die in <1s on "Resume
        # config mismatch" and the guard bug below looped it all night.
        "--dtype", "bf16", "--dropout", "0.1", "--resume",
    ]


def bootstrap_stage(stage: dict) -> bool:
    """Stage weights from the previous stage into this stage's fresh dir."""
    source = stage.get("source_dir")
    if not source:
        return True     # stage 1 resumes in place
    target = stage_dir(stage)
    if (target / "checkpoint.pt").exists():
        return True     # already bootstrapped (previous attempt may have died)
    proc = subprocess.run(
        [str(PYEXE), str(REPO / "scripts" / "bootstrap_sft_state.py"),
         "--source", source, "--target", str(target),
         "--tokenizer", "D:/hwk-data/tokenizer/hwk_spm.model"],
        capture_output=True, text=True, timeout=600)
    ok = proc.returncode == 0 and (target / "checkpoint.pt").exists()
    if ok:
        log(f"bootstrapped {source} -> {target}")
    else:
        log(f"bootstrap FAILED {source} -> {target}: "
            f"{(proc.stderr or proc.stdout).strip()[-300:]}")
    return ok


def launch_stage(stage: dict) -> bool:
    if not gpu_is_free():
        log(f"stage {stage['stage']}: GPU busy - launch deferred")
        return False
    if not bootstrap_stage(stage):
        return False
    pid = launch_detached(build_stage_command(stage),
                          f"phase_d_train_stage{stage['stage']}")
    if pid:
        log(f"stage {stage['stage']} launched (pid {pid}) -> "
            f"{stage['output_dir']} corpora={stage['corpora'] or stage3_corpora_arg()} "
            f"max_steps={stage['max_steps']}")
        save_state(stage)
        return True
    return False


def save_state(stage: dict) -> None:
    # 2026-09-22 08:00 lesson: this used to zero `restarts` on EVERY launch,
    # so the crash-loop guard could never count past 1 and a trainer dying
    # instantly (bad flag) looped all night. restarts is now preserved here
    # and reset only where a launch is VERIFIED healthy (reset_restart_count).
    previous = load_state() or {}
    try:
        STATE_FILE.write_text(json.dumps({
            "stage": stage["stage"],
            "max_steps": stage["max_steps"],
            "output_dir": stage["output_dir"],
            "launched_at": datetime.now().isoformat(timespec="seconds"),
            "restarts": int(previous.get("restarts", 0)),
        }), encoding="utf-8")
    except OSError:
        pass


def load_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def active_stage() -> dict:
    """The stage the state file says is running (default: stage 1)."""
    state = load_state()
    if state:
        for stage in STAGES:
            if stage["stage"] == state.get("stage"):
                return stage
    return STAGES[0]


def tokenizer_running() -> tuple[bool, int | None]:
    """Is ANY live python process running tokenize_corpus?

    (2026-09-22 08:35 lesson: the lockfile holds the pid launch_detached
    returns - the venv SHIM, which exits as soon as it spawns the real
    python312 child. The lock therefore always held a dead pid, every cycle
    read "stale lock", and the watchdog relaunched the tokenizer on EVERY
    cycle while the previous one was still writing - concurrent writers on
    one corpus again. Scanning actual process command lines is the only
    signal that does not depend on which pid got recorded.)"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
             "| Where-Object { $_.CommandLine -match 'tokenize_corpus' } "
             "| Select-Object -First 1 -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False, None   # cannot verify -> caller may relaunch; wipe guard below still protects dirs
    out = (proc.stdout or "").strip()
    if not out.isdigit():
        return False, None
    return True, int(out)


def ensure_tokenization() -> None:
    """Start a detached CPU-only tokenizer for corpora that lack shards.
    Skips when everything is tokenized OR a verified tokenizer is alive."""
    # Liveness check FIRST (2026-09-22 08:28 lesson): a live tokenizer's
    # target dirs look exactly like "partial runs" (shards, no manifest)
    # from the outside. Wiping before checking liveness deleted a running
    # tokenizer's finished shards out from under it.
    running, pid = tokenizer_running()
    if running:
        return  # a live tokenizer owns the corpora - never touch its dirs
    if pid is not None:
        try:
            TOKENIZE_LOCK.unlink()
        except OSError:
            pass
        log(f"stale tokenizer lock (pid {pid} not a live tokenizer) - relaunching")
    todo = [name for name in TOKENIZE_CORPORA if corpus_needs_tokens(name)]
    if not todo:
        return
    # A partial run (shards but no manifest) has unknown shard boundaries;
    # tokenize_corpus.py cannot resume it, so wipe it for a clean redo.
    for name in todo:
        out = tokens_root() / name
        if sorted(out.glob("train_*.bin")) and not (out / "manifest.json").exists():
            shutil.rmtree(out, ignore_errors=True)
            log(f"wiped partial (no manifest) token dir: {name}")
    cmd = [str(PYEXE), str(REPO / "tokenize_corpus.py"),
           "--tokenizer", "D:/hwk-data/tokenizer/hwk_spm.model"]
    for name in todo:
        cmd += ["--corpus", name]
    child = launch_detached(cmd, "tokenize_new")
    if child:
        try:
            TOKENIZE_LOCK.write_text(str(child), encoding="utf-8")
        except OSError:
            pass
        log(f"tokenizer launched for {todo} (pid {child}, CPU-only)")


def bump_restart_count(stage: dict) -> int:
    """Track consecutive restarts of the SAME stage (crash-loop guard)."""
    state = load_state() or {}
    count = int(state.get("restarts", 0)) + 1
    state.update({"restarts": count})
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass
    return count


def reset_restart_count(stage: dict) -> None:
    state = load_state() or {}
    if state.get("restarts"):
        state["restarts"] = 0
        try:
            STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass


def check_cycle(heartbeat: bool = True) -> str:
    """One watchdog cycle. Returns an action string (logged on action)."""
    stage = active_stage()
    ensure_tokenization()          # resume tokenization if it died mid-run
    alive, reason = trainer_healthy(stage)
    if not alive:
        state = load_state() or {}
        if int(state.get("restarts", 0)) >= 3:
            # crash-loop guard (2026-09-22): a stage that dies 3 times in a
            # row (e.g. VRAM OOM at a new context) must NOT burn the whole
            # night in a restart loop. Stop and say so loudly.
            if heartbeat:
                log(f"stage {stage['stage']}: restart limit reached "
                    f"({state.get('restarts')}x) - leaving it down: {reason}")
            return "restart-limit"
        count = bump_restart_count(stage)
        log(f"stage {stage['stage']}: trainer DOWN ({reason}) - restarting "
            f"(attempt {count}/3)")
        if launch_stage(stage):
            return "restarted"
        return "restart-failed"
    reset_restart_count(stage)
    if "COMPLETE" in reason:
        nxt = next((s for s in STAGES if s["stage"] == stage["stage"] + 1), None)
        if nxt is None:
            return f"all stages complete ({reason})"
        if nxt["stage"] == 3 and stage3_corpora_arg() is None:
            # Never launch stage 3 on a shrunken corpus list: wait for the
            # tokenizer to land law/medical/... shards first.
            return ("stage 3 deferred - no new corpora tokenized yet "
                    "(stage 1 trainer stays COMPLETE; re-check next cycle)")
        log(f"stage {stage['stage']} done -> advancing to stage {nxt['stage']}")
        if launch_stage(nxt):
            return f"advanced-to-{nxt['stage']}"
        return "advance-failed"
    if heartbeat:
        log(f"ok: {reason}")
    return f"ok: {reason}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="run a single check cycle and exit")
    parser.add_argument("--duration-hours", type=float, default=10.0,
                        help="give up after this long (safety window)")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    log(f"=== phase D watchdog on duty (pid {os.getpid()}) ===")
    ensure_tokenization()
    deadline = time.time() + args.duration_hours * 3600
    cycles = 0
    while True:
        cycles += 1
        try:
            # Heartbeat line every 10th cycle (~10 min) keeps the log readable
            # while still proving liveness to the morning report.
            check_cycle(heartbeat=(cycles % 10 == 0))
        except Exception as exc:  # noqa: BLE001 - never die mid-night
            log(f"cycle error (continuing): {exc!r}")
        if args.once:
            return 0
        if time.time() > deadline:
            log("watchdog window over - going to sleep")
            return 0
        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
