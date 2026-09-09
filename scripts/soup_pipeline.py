"""Post-Phase-A pipeline: teacher baseline -> re-SFT on sft_v2 -> gated exam.

Runs the full GPU sequence the moment Phase A's GPU is free (it refuses or
waits while training is live - never touches a running run):

  1. WAIT until the GPU has no compute processes and training.log is idle 15+ min
  2. SERVE the teacher (Soup: Qwen2.5-1.5B-Instruct) on 127.0.0.1:20129
  3. BASELINE EXAM - the 24-case tool-calling exam via scripts/soup_exam.py
  4. RE-SFT - `soup train` on D:/hwk-data/soup/sft_v2.jsonl (QLoRA, <=3 epochs)
  5. TUNED EXAM - grade the tuned adapter with the SAME grader
  6. VERDICT - tuned must BEAT baseline to be promoted; writes a markdown
     report + updates the live-status doc.

Safety:
- refuses to start while Phase A training is alive (check by log idle + GPU)
- every stage is logged to D:/hwk-data/soup_pipeline.log; a stage failure
  stops the pipeline (no blind retries on a live GPU)
- only Soup's own output dir is touched; Aali's checkpoints are never written

Usage:
  python scripts/soup_pipeline.py            # wait for GPU, then run all
  python scripts/soup_pipeline.py --no-wait  # run immediately (GPU must be free)
  python scripts/soup_pipeline.py --dry-run  # print the plan, run nothing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover
    psutil = None

ROOT = Path(__file__).resolve().parents[1]
SOUP_EXE = Path(os.getenv("SOUP_EXE", "D:/hwk-tools/soup-venv/Scripts/soup.exe"))
TRAINING_LOG = Path("D:/hwk-data/training.log")
PIPELINE_LOG = Path("D:/hwk-data/soup_pipeline.log")
EXAM_PROMPTS = Path("D:/hwk-data/soup/exam_prompts.jsonl")
SFT_V2 = Path("D:/hwk-data/soup/sft_v2.jsonl")
SOUP_CONFIG = ROOT / "soup.yaml"
REPORTS = Path("D:/hwk-data/soup")
BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
PORT = 20129
IDLE_MINUTES = 15
LOG_TAIL = Path("D:/hwk-data/soup_pipeline_last_stage.txt")

_model_re = re.compile(r"Qwen/Qwen2\.5-(\d+\.?\d*)B")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    line = f"[{_stamp()}] {message}"
    print(line, flush=True)
    try:
        with PIPELINE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def gpu_free() -> tuple[bool, str]:
    """Training is not running: no python-family compute process on the GPU
    AND training.log idle >= IDLE_MINUTES.

    On Windows browsers/DWM also hold CUDA contexts, so "no processes at all"
    would never be true - only python-family processes (our trainer) block.
    The log-idle check is the primary signal: the trainer writes every ~1.5 min.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        python_pids = [
            line for line in out.splitlines()
            if re.search(r"python|ptxas|soup", line, re.IGNORECASE)
        ]
        if python_pids:
            return False, f"python-family compute processes on GPU: {python_pids}"
    except (OSError, subprocess.TimeoutExpired):
        return False, "could not query GPU state - refusing to assume"
    try:
        mtime = TRAINING_LOG.stat().st_mtime
        idle_minutes = (time.time() - mtime) / 60
        if idle_minutes < IDLE_MINUTES:
            return False, f"training.log written {idle_minutes:.1f} min ago (<{IDLE_MINUTES})"
    except OSError:
        pass  # no training log: nothing to protect
    return True, "no python compute on GPU and training log idle"


def wait_for_gpu(no_wait: bool, timeout_hours: float = 12.0) -> bool:
    if no_wait:
        ok, reason = gpu_free()
        if not ok:
            log(f"REFUSING to start: {reason}")
            return False
        log(f"GPU confirmed free: {reason}")
        return True
    deadline = time.time() + timeout_hours * 3600
    while time.time() < deadline:
        ok, reason = gpu_free()
        if ok:
            log(f"GPU confirmed free: {reason}")
            return True
        log(f"waiting (recheck in 5 min): {reason}")
        time.sleep(300)
    log("timed out waiting for a free GPU")
    return False


def _adapter_dir() -> Path:
    """Latest Soup output that actually contains trained adapter weights."""
    out_root = REPORTS / "tuned"
    candidates = sorted(out_root.glob("checkpoint-*"), reverse=True)
    for candidate in candidates:
        if (candidate / "adapter_model.safetensors").exists():
            return candidate
    return out_root


def _model_for_adapter() -> str:
    """The base model the adapter was trained on (from soup.yaml)."""
    try:
        text = SOUP_CONFIG.read_text(encoding="utf-8")
        match = re.search(r"^base:\s*(\S+)", text, re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    return BASE_MODEL


LOCAL_TEACHER = Path("D:/hwk-models/Qwen2.5-1.5B-Instruct")


def resolve_teacher_model() -> str:
    """Soup expects a LOCAL model directory — it does not fetch from the hub.
    Use the pre-downloaded copy; download it if missing (scripts/download_teacher.py
    does the same thing standalone)."""
    if not LOCAL_TEACHER.exists():
        log(f"teacher model not found at {LOCAL_TEACHER} - downloading (~3GB)…")
        from huggingface_hub import snapshot_download

        snapshot_download(BASE_MODEL, local_dir=str(LOCAL_TEACHER))
        log("teacher model downloaded")
    return str(LOCAL_TEACHER)


def serve_teacher() -> subprocess.Popen | None:
    try:
        model_ref = resolve_teacher_model()
    except Exception as exc:  # noqa: BLE001
        log(f"failed to obtain the teacher model: {exc}")
        return None
    log(f"starting Soup server on port {PORT} ({model_ref})")
    try:
        process = subprocess.Popen(
            [str(SOUP_EXE), "serve", "--model", model_ref, "--port", str(PORT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        log(f"failed to start soup serve: {exc}")
        return None
    import urllib.request
    for _ in range(90):  # up to 15 min for the server to load the local model
        time.sleep(10)
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/v1/models", timeout=5
            ) as response:
                if response.status == 200:
                    log("server is up")
                    return process
        except Exception:  # noqa: BLE001 - not up yet
            continue
    log("server did not become ready in 15 minutes")
    process.terminate()
    return None


def run_exam(stage: str, model: str) -> dict | None:
    """Grade `model` on the 24-case exam; writes soup_exam_report_<stage>.json."""
    report_path = REPORTS / f"soup_exam_report_{stage}.json"
    log(f"exam [{stage}] model={model}")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "soup_exam.py"),
         "--base-url", f"http://127.0.0.1:{PORT}/v1",
         "--model", model,
         "--output", str(report_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=3600,
    )
    LOG_TAIL.write_text((completed.stdout or "")[-2000:] + (completed.stderr or "")[-2000:],
                        encoding="utf-8")
    if completed.returncode != 0:
        log(f"exam [{stage}] FAILED (exit {completed.returncode}) - see {LOG_TAIL}")
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log(f"exam [{stage}] produced no readable report")
        return None
    score = report.get("score", "?")
    log(f"exam [{stage}] score: {score}")
    return report


def run_training() -> bool:
    if not SFT_V2.exists():
        log(f"missing dataset {SFT_V2} - run scripts/build_aali_sft_v2.py first")
        return False
    # soup.yaml already points at sft_v2 in-repo; verify instead of rewriting,
    # so a stale local edit fails fast instead of training on the wrong data.
    config_text = SOUP_CONFIG.read_text(encoding="utf-8").replace("\\", "/")
    if str(SFT_V2).replace("\\", "/") not in config_text:
        log(f"error: {SOUP_CONFIG} does not point at {SFT_V2} - fix data.train first")
        return False
    log(f"starting soup train on {SFT_V2.name}")
    completed = subprocess.run(
        [str(SOUP_EXE), "train", "--config", str(SOUP_CONFIG), "--yes"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=6 * 3600,
    )
    LOG_TAIL.write_text((completed.stdout or "")[-4000:] + (completed.stderr or "")[-4000:],
                        encoding="utf-8")
    if completed.returncode != 0:
        log(f"soup train FAILED (exit {completed.returncode}) - see {LOG_TAIL}")
        return False
    log("soup train finished")
    return True


def write_verdict(baseline: dict | None, tuned: dict | None) -> str:
    def _score(report: dict | None) -> int:
        if not report:
            return -1
        match = re.match(r"(\d+)/", str(report.get("score", "")))
        return int(match.group(1)) if match else -1

    baseline_score, tuned_score = _score(baseline), _score(tuned)
    if baseline_score < 0 or tuned_score < 0:
        verdict = "INCOMPLETE - a stage failed; see soup_pipeline.log"
    elif tuned_score > baseline_score:
        verdict = ("PROMOTE - the tuned model beats the baseline; adapter is "
                   f"at {_adapter_dir()}")
    elif tuned_score == baseline_score:
        verdict = "NO-GO for promotion (tie) - keep the baseline, consider more data"
    else:
        verdict = "NO-GO for promotion (regression) - keep the baseline"
    lines = [
        "# Soup re-SFT pipeline report",
        f"_generated {_stamp()}_",
        "",
        "| stage | model | score |",
        "|---|---|---|",
        f"| baseline | {BASE_MODEL} | {baseline.get('score') if baseline else 'failed'} |",
        f"| tuned | {_model_for_adapter()} + adapter | {tuned.get('score') if tuned else 'failed'} |",
        "",
        f"**Verdict: {verdict}**",
        "",
        "Gate: promotion requires the tuned model to strictly beat the baseline",
        "on the same 24-case exam (memory + security + anti-hallucination).",
    ]
    report_text = "\n".join(lines)
    (REPORTS / "resft_pipeline_report.md").write_text(report_text, encoding="utf-8")
    # Promotion gate: when the tuned adapter beats the baseline, record it as
    # Aali's own model so the runtime brain can switch to it (see agent_loop).
    if verdict.startswith("PROMOTE"):
        promoted = {
            "adapter_dir": str(_adapter_dir()),
            "base_model": _model_for_adapter(),
            "base_url": f"http://127.0.0.1:{PORT}/v1",
            "port": PORT,
            "baseline_score": baseline_score,
            "tuned_score": tuned_score,
            "promoted_at": _stamp(),
        }
        (REPORTS / "promoted.json").write_text(
            json.dumps(promoted, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"PROMOTED -> {promoted['adapter_dir']} (score {tuned_score} > {baseline_score})")
    log(f"verdict: {verdict}")
    return report_text


def _acquire_lock() -> bool:
    """Singleton guard: only one pipeline instance may wait/serve/train."""
    if psutil is None:  # cannot verify PIDs; allow start rather than block
        return True
    lock_path = Path(os.environ.get("TEMP", "/tmp")) / "hwk_soup_pipeline.lock"
    try:
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text(encoding="utf-8").strip() or "0")
            except ValueError:
                pid = 0
            if pid > 0 and pid != os.getpid() and psutil.pid_exists(pid):
                existing = psutil.Process(pid)
                if "python" in existing.name().lower():
                    return False
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-Phase-A Soup pipeline")
    parser.add_argument("--no-wait", action="store_true",
                        help="do not wait; refuse unless the GPU is free now")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and exit")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    if args.dry_run:
        ok, reason = gpu_free()
        print(f"GPU free: {ok} ({reason})")
        print(f"1. serve {BASE_MODEL} on :{PORT}")
        print(f"2. baseline exam -> {REPORTS / 'soup_exam_report_baseline.json'}")
        print(f"3. soup train on {SFT_V2.name} (via {SOUP_CONFIG})")
        print(f"4. tuned exam   -> {REPORTS / 'soup_exam_report_tuned.json'}")
        print(f"5. verdict      -> {REPORTS / 'resft_pipeline_report.md'}")
        return 0

    done_marker = REPORTS / "resft_pipeline_report.md"
    if done_marker.exists():
        # A failed run also writes a verdict; only a real completion blocks a retry.
        if "INCOMPLETE" not in done_marker.read_text(encoding="utf-8"):
            log("pipeline already completed (verdict exists) - nothing to do")
            return 0
        log("previous run ended INCOMPLETE - retrying")
    log("=== soup pipeline start ===")
    if not _acquire_lock():
        log("another pipeline instance already holds the lock - exiting")
        return 3
    if not wait_for_gpu(args.no_wait):
        return 2

    server = serve_teacher()
    if server is None:
        write_verdict(None, None)
        return 1
    try:
        baseline = run_exam("baseline", BASE_MODEL)
        if baseline is None:
            write_verdict(None, None)
            return 1
        # OOM lesson (2026-09-08 04:48 run): soup train ran WHILE the exam
        # server still held VRAM -> CUDA OOM at batch_size=1. Stop the server
        # and let the driver release memory BEFORE training starts.
        log("stopping exam server to free VRAM for training")
        server.terminate()
        server.wait(timeout=60)
        time.sleep(15)  # driver async release
        server = None
        if not run_training():
            write_verdict(baseline, None)
            return 1
        tuned = run_exam("tuned", _model_for_adapter())
        if tuned is None:
            write_verdict(baseline, None)
            return 1
        report_text = write_verdict(baseline, tuned)
        log("=== soup pipeline done ===")
        # When promoted, keep serving the TUNED adapter so Aali's own brain
        # (agent_loop.promoted_own_model -> promoted.json base_url) is live.
        if report_text.startswith("# Soup re-SFT pipeline report") and "Verdict: PROMOTE" in report_text:
            adapter = _adapter_dir()
            try:
                # NOTE: a dedicated variable - reusing `server` made the
                # finally below terminate the freshly promoted server.
                promoted = subprocess.Popen(
                    [str(SOUP_EXE), "serve", "--model", str(adapter), "--port", str(PORT)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                )
                log(f"serving promoted adapter on :{PORT} (pid {promoted.pid})")
            except OSError as exc:
                log(f"could not serve promoted adapter ({exc}) - Aali falls back")
        return 0
    finally:
        if server is not None:
            server.terminate()

    try:
        (Path(os.environ.get("TEMP", "/tmp")) / "hwk_soup_pipeline.lock").unlink()
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
