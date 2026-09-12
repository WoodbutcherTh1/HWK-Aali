#!/usr/bin/env python3
"""Aali status digest - aggregates every log into D:/hwk-data/STATUS.md.

Runs every 30 min via the scheduled task "HWK StatusDigest" and can always be
run by hand: python scripts/status_digest.py

Sections: alerts first (only things needing attention), then Phase B training,
caretaker, graduation pipeline, Pi-CI, disks. Pace/ETA are computed across
consecutive digest runs (state in D:/hwk-data/.status_digest_state.json), so
they self-correct and never rely on the trainer's own garbage ETA column.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("D:/hwk-data")
STATUS_MD = DATA / "STATUS.md"
STATE = DATA / ".status_digest_state.json"
TRAIN_LOG = DATA / "context_training.log"
CARE_LOG = DATA / "caretaker.log"
SOUP_LOG = DATA / "soup_pipeline.log"
REPORT = DATA / "soup" / "resft_pipeline_report.md"
PI_LOG = Path("D:/hwk-data/pi_ci/ci_status.log")
PI_RESULT = Path("D:/hwk-data/pi_ci/result.json")
TARGET_STEPS = 100_000
STALL_SEC = 15 * 60


def age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    return f"{seconds / 3600:.1f} h ago"


def tail(path: Path, n: int = 8) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def gpu_summary() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        return out.replace("\n", " | ")
    except Exception:
        return "n/a"


def phase_b() -> tuple[list[str], dict]:
    lines = tail(TRAIN_LOG, 40)
    step = eval_loss = None
    for line in reversed(lines):
        m = re.search(r"step=(\d+)", line)
        if m and step is None:
            step = int(m.group(1))
        e = re.search(r"eval_loss=([\d.]+)", line)
        if e and eval_loss is None:
            eval_loss = float(e.group(1))
        if step is not None and eval_loss is not None:
            break
    age = age_seconds(TRAIN_LOG)
    rows, info = [], {"step": step, "pace": None}
    if step is None:
        rows.append(("⚠️", "training log missing/unreadable"))
        return rows, info
    pct = step / TARGET_STEPS * 100
    if step >= TARGET_STEPS:
        rows.append(("✅", f"Phase B COMPLETE: step {step:,}/{TARGET_STEPS:,}"))
        return rows, info
    if age is not None and age > STALL_SEC:
        rows.append(("⚠️", f"trainer STALLED - last log write {fmt_age(age)} "
                           f"(step {step:,}, {(step / 100):.1f}%)"))
    else:
        rows.append(("✅", f"step {step:,}/{TARGET_STEPS:,} ({pct:.1f}%), "
                           f"eval_loss {eval_loss}, last write {fmt_age(age)}"))
    # pace/ETA across digest runs
    now = time.time()
    try:
        prev = json.loads(STATE.read_text(encoding="utf-8"))
        dt = now - float(prev["ts"])
        if prev.get("step") and step >= prev["step"] and dt > 300:
            pace = (step - prev["step"]) / (dt / 60)  # steps/min
            info["pace"] = round(pace, 1)
            if pace > 0:
                eta_min = (TARGET_STEPS - step) / pace
                rows.append(("⏳", f"pace {pace:.0f} steps/min -> "
                                   f"~{eta_min / 60:.1f} h to finish"))
    except (OSError, ValueError, KeyError):
        pass
    STATE.write_text(json.dumps({"ts": now, "step": step}), encoding="utf-8")
    return rows, info


def caretaker() -> list[tuple[str, str]]:
    lines = tail(CARE_LOG, 8)
    if not lines:
        return [("⚠️", "no caretaker log at all")]
    age = age_seconds(CARE_LOG)
    # "going to sleep" is the caretaker's LAST line on a normal end of duty
    # - an old log ending with it is a completed shift, not a stall.
    if "going to sleep" in lines[-1]:
        return [("✅", f"duty ended normally {fmt_age(age)} - relaunch "
                       "night_caretaker.py for the next shift")]
    if age is not None and age > STALL_SEC:
        return [("⚠️", f"caretaker STALLED - last write {fmt_age(age)} "
                       "(killed mid-duty? relaunch night_caretaker.py)")]
    alive = next((l for l in reversed(lines) if "Phase B alive" in l), None)
    return [("✅", f"on duty, last check {fmt_age(age)}"
                   + (f" - '{alive.strip()}'" if alive else ""))]


def _log_line_age(line: str) -> float | None:
    """Age in seconds of a '[YYYY-MM-DD HH:MM:SS] ...' log line (the soup
    pipeline stamps UTC). None when the line has no parseable stamp."""
    match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
    if not match:
        return None
    try:
        stamp = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        return time.time() - stamp.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _last_pipeline_activity(lines: list[str]) -> float | None:
    """Age of the newest line the PIPELINE itself wrote. Test suites pollute
    the real log (write_verdict lines carry 'pytest-of'); those lines are
    skipped, so file mtimes and fresh-looking junk cannot fake activity."""
    for line in reversed(lines):
        if "pytest-of" in line:
            continue
        age = _log_line_age(line)
        if age is not None:
            return age
    return None


def pipeline() -> list[tuple[str, str]]:
    # Verdicts are read from the report FILE (rewritten fresh by every completed
    # run), never from old log lines - history must not read like news.
    # 2026-09-12 17:03 lesson (the reboot killed attempt 8 in its first
    # minute): a fresh "start" line is NOT proof of life - a run that died
    # right after starting leaves a young start marker and a silent log.
    # A run counts as RUNNING only when the pipeline WROTE something
    # recently (stages, smoke probes...); a fresh start marker over a silent
    # log is a STALLED run that needs a relaunch.
    lines = tail(SOUP_LOG, 10)
    for line in reversed(lines):
        if "pytest-of" in line:
            continue
        if "=== soup pipeline start ===" in line:
            age = _log_line_age(line)
            if age is not None and age < 6 * 3600:
                activity = _last_pipeline_activity(lines)
                if activity is not None and activity <= STALL_SEC:
                    return [("🔄", f"pipeline RUNNING (last activity "
                                   f"{fmt_age(activity)})")]
                # fresh start marker but no recent activity: the run died
                # just after starting (reboot / job kill) - say so.
                silent_age = (activity if activity is not None
                              else age_seconds(SOUP_LOG))
                silent_for = fmt_age(silent_age)
                if silent_for.endswith(" ago"):
                    silent_for = silent_for[: -len(" ago")]
                return [("⚠️", f"pipeline STALLED - started {fmt_age(age)} "
                               f"but silent for {silent_for} (relaunch "
                               "soup_pipeline.py)")]
            break
    # Mid-training the start marker scrolls past tail(10) - a recent line the
    # PIPELINE itself wrote proves it is alive (the smoke watcher writes
    # every ~6.5 min during training; pytest pollution is filtered out).
    activity = _last_pipeline_activity(lines)
    if activity is not None and activity < 10 * 60:
        return [("🔄", f"pipeline RUNNING (log last write {fmt_age(activity)})")]
    age = age_seconds(REPORT)
    if age is not None and age < 48 * 3600:
        try:
            text = REPORT.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [("❓", "verdict file exists but is unreadable")]
        if "PROMOTE" in text:
            return [("🎉", f"GRADUATED - verdict PROMOTE, {fmt_age(age)} "
                           "(adapter promoted to live brain)")]
        if "NO-GO" in text:
            return [("⚠️", f"verdict NO-GO {fmt_age(age)} - baseline kept, "
                           "graduation failed")]
        if "INCOMPLETE" in text:
            return [("⚠️", f"last run INCOMPLETE {fmt_age(age)} - a stage "
                           "failed (see soup_pipeline.log); the next launch "
                           "auto-retries")]
        return [("❓", "verdict file present but contains no verdict")]
    return [("💤", "idle - awaiting Phase B completion (no fresh verdict)")]


def pi_ci() -> list[tuple[str, str]]:
    try:
        result = json.loads(PI_RESULT.read_text(encoding="utf-8"))
        status = result.get("status", "?")
        passed, failed = result.get("passed"), result.get("failed")
        commit = result.get("commit", "?")
        icon = "✅" if status == "ok" else "⚠️"
        line = (f"last Pi run: {status} ({passed} passed / {failed} failed, "
                f"{commit}), {fmt_age(age_seconds(PI_RESULT))}")
        return [(icon, line)]
    except (OSError, ValueError):
        return [("⏳", "Pi-CI: no result yet (waiting on one-time pi_setup.sh - "
                       "see D:/hwk-data/pi_ci/README.md)")]


def disks() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for drive in ("D:/", "X:/"):
        try:
            total, _, free = shutil.disk_usage(drive)
            pct = free / total * 100
            icon = "✅" if pct >= 20 else "⚠️"
            rows.append((icon, f"{drive[0]}: {free / (1 << 30):.0f}G free "
                               f"({pct:.0f}%) - {'PASS' if pct >= 20 else 'BELOW'} 20% rule"))
        except OSError:
            rows.append(("⚠️", f"{drive[0]}: unavailable"))
    return rows


def main() -> None:
    now = datetime.now(timezone.utc).astimezone()
    train_rows, _ = phase_b()
    care_rows = caretaker()
    pipe_rows = pipeline()
    pi_rows = pi_ci()
    disk_rows = disks()
    alerts = [msg for icon, msg in train_rows + care_rows + pipe_rows + pi_rows + disk_rows
              if icon == "⚠️"]

    out = [f"# Aali status — auto-updated {now:%Y-%m-%d %H:%M} local",
           f"_GPU: {gpu_summary()}_", ""]
    out += ["## 🚨 Needs attention" if alerts else "## ✅ All clear"]
    out += [f"- {a}" for a in alerts] if alerts else ["- nothing needs you right now"]
    out += ["", "## Phase B training"]
    out += [f"- {m}" for _, m in train_rows]
    out += ["", "## Night caretaker"]
    out += [f"- {m}" for _, m in care_rows]
    out += ["", "## Graduation pipeline"]
    out += [f"- {m}" for _, m in pipe_rows]
    out += ["", "## Pi-CI (192.168.1.9)"]
    out += [f"- {m}" for _, m in pi_rows]
    out += ["", "## Disks"]
    out += [f"- {m}" for _, m in disk_rows]
    out += ["", "_Regenerated every 30 min by scripts/status_digest.py "
                "(scheduled task HWK StatusDigest)._"]
    STATUS_MD.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {STATUS_MD} ({len(alerts)} alert(s))")


if __name__ == "__main__":
    main()
