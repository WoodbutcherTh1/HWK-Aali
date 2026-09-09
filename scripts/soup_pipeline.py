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
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover
    psutil = None

# Shared GPU gate (scripts/wait_gpu_free.py) - one source of truth for
# "is the 8GB card free", used by every launcher on this machine.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wait_gpu_free as gate  # noqa: E402
from soup_exam import SMOKE_CASE_IDS  # noqa: E402

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
# The smoke probe serves on its OWN port and on CPU, so it can run while the
# GPU trains and can never collide with the exam/promotion port.
SMOKE_PORT = PORT + 1
SMOKE_MIN_PASS = 3            # of 6 probe cases (2x img, 2x halluc, security, memory)
SMOKE_MAX_CONSECUTIVE_FAILS = 2  # abort training at the 2nd consecutive failed probe
SMOKE_POLL_S = 390            # watcher cadence (~6.5 min - one probe cycle)
IDLE_MINUTES = 15

# The soup train process while it runs (so the smoke watcher can abort it).
training_process: subprocess.Popen | None = None
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
    # Shared gate (scripts/wait_gpu_free.py): nvidia-smi compute processes +
    # VRAM level. The training-log-idle guard below stays local - it is this
    # pipeline's extra protection against a trainer whose CUDA context has
    # not registered yet.
    ok, reason = gate.card_is_safe(require_training_log_idle=False)
    if not ok:
        return False, reason
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
    ok, reason = gate.wait_until_safe(
        timeout_s=int(timeout_hours * 3600), poll_s=300,
        on_wait=lambda why: log(f"waiting (recheck in 5 min): {why}"))
    if ok:
        log(f"GPU confirmed free: {reason}")
        return True
    log("timed out waiting for a free GPU")
    return False


def _ckpt_step(path: Path) -> int:
    """Numeric step of a 'checkpoint-<step>' dir (lexic sort puts 10000
    before 9000 - a promoted-adapter bug waiting to happen)."""
    match = re.search(r"checkpoint-(\d+)$", path.name)
    return int(match.group(1)) if match else -1


def _adapter_dir() -> Path:
    """Latest Soup output that actually contains trained adapter weights."""
    out_root = REPORTS / "tuned"
    candidates = sorted(out_root.glob("checkpoint-*"), key=_ckpt_step, reverse=True)
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


def _wait_http(url: str, timeout_s: int = 900, log_fn=None) -> bool:
    """Poll a URL until it answers 2xx (up to 15 min for a model load)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(10)
    if log_fn is not None:
        log_fn(f"{url} not ready after {timeout_s}s")
    return False


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


def run_exam(stage: str, model: str, ids: list[str] | None = None) -> dict | None:
    """Grade `model` on the exam; writes soup_exam_report_<stage>.json.
    ids=None runs the full 26-case exam; a subset probes only those cases."""
    report_path = REPORTS / f"soup_exam_report_{stage}.json"
    log(f"exam [{stage}] model={model}")
    command = [
        sys.executable, str(ROOT / "scripts" / "soup_exam.py"),
        "--base-url", f"http://127.0.0.1:{PORT}/v1",
        "--model", model,
        "--output", str(report_path),
    ]
    if ids:
        command += ["--ids", ",".join(ids)]
    completed = subprocess.run(
        command,
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


def wait_vram_clear(timeout_s: int = 240) -> None:
    """Wait until used VRAM drops to desktop-idle levels (<1.5 GB).

    Terminating the exam server is not enough on Windows: the driver
    releases its VRAM asynchronously, and the old fixed 15s sleep still
    OOM'd soup train (2026-09-09 09:50 - the server's ~4GB was still
    held when training started). Poll until the card is actually clear.
    Delegates to the shared gate (scripts/wait_gpu_free.py).
    """
    ok, reason = gate.wait_until_safe(timeout_s=timeout_s,
                                      on_wait=lambda why: log(f"VRAM busy: {why}"))
    if ok:
        log(f"VRAM released ({reason}) - ready to train")
    else:
        log(f"VRAM still busy after {timeout_s}s ({reason}) - training may OOM")


# ---------------------------------------------------------------------------
# Smoke probe: cheap CPU-served exam between training checkpoints, so a
# broken adapter dies in ~1h instead of burning 3h for a 0/26 verdict
# (2026-09-09: the tuned exam graded a DEAD endpoint and scored 0/26 -
# the smoke gate also forces a live serving check before any real exam).
# ---------------------------------------------------------------------------

def _probe_endpoint(base_url: str, prompt: str, model: str, timeout: int = 120) -> str:
    """One chat completion; empty string on any failure (never raises)."""
    try:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": 0.0,
        }).encode("utf-8")
        request = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return str(body["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001 - probe failures are data, not crashes
        return ""


def _cpu_serving_ok() -> bool:
    """Cheap guard before relying on the CPU-served probe: the soup venv
    must import torch and report enough threads to serve while the GPU
    trains. A weak machine skips the gate for the whole run (logged)."""
    probe = subprocess.run(
        [str(Path(SOUP_EXE).parent / "python.exe"), "-c",
         "import torch; print(torch.get_num_threads())"],
        capture_output=True, text=True, timeout=120,
    )
    try:
        threads = int(probe.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        threads = 0
    # torch reports PHYSICAL cores (6 on the owner's 12-thread i7) - do not
    # demand logical-core counts or the gate disables itself in production.
    if probe.returncode != 0 or threads < 4:
        log(f"smoke probe disabled: soup venv CPU check failed "
            f"(exit {probe.returncode}, threads={threads})")
        return False
    return True


SMOKE_LOG_TAIL = Path("D:/hwk-data/smoke_probe_tail.txt")


def run_smoke_probe(stage: str) -> dict | None:
    """Serve the latest adapter on CPU (:SMOKE_PORT) and grade the 6-case
    subset. Returns the graded report or None when the probe is unavailable
    (no checkpoint yet / CPU serve not viable). Never touches the GPU."""
    adapter = _adapter_dir()
    if not (adapter / "adapter_model.safetensors").exists():
        log(f"smoke probe [{stage}]: no adapter checkpoint yet - skipping")
        return None
    try:
        probe = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "soup_probe_smoke.py"),
             "--model", str(adapter), "--port", str(SMOKE_PORT),
             "--ids", ",".join(SMOKE_CASE_IDS),
             "--output", str(REPORTS / f"smoke_{stage}.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        # A timed-out probe is 'unavailable', never a failed probe - it must
        # not crash the watcher thread or count toward the abort threshold.
        log(f"smoke probe [{stage}] timed out after 1800s - treating as unavailable")
        return None
    try:
        SMOKE_LOG_TAIL.write_text(
            (probe.stdout or "")[-2000:] + (probe.stderr or "")[-2000:],
            encoding="utf-8")
    except OSError:
        pass
    if probe.returncode != 0:
        log(f"smoke probe [{stage}] FAILED (exit {probe.returncode}) "
            f"- see {SMOKE_LOG_TAIL}")
        return None
    try:
        report = json.loads((REPORTS / f"smoke_{stage}.json")
                            .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log(f"smoke probe [{stage}] produced no readable report")
        return None
    log(f"smoke probe [{stage}] score: {report.get('score')} "
        f"(need >= {SMOKE_MIN_PASS}/{len(SMOKE_CASE_IDS)})")
    return report


def _probe_passes(report: dict | None) -> bool:
    if not report:
        return False
    match = re.match(r"(\d+)/", str(report.get("score", "")))
    return (int(match.group(1)) if match else 0) >= SMOKE_MIN_PASS


def smoke_watcher(stop: threading.Event, abort_reason: list[str]) -> None:
    """Probe the newest adapter checkpoint every ~6.5 min while training runs
    (first checkpoints appear after ~epoch 1). Two consecutive FAILED probes
    = the run is broken: terminate the trainer. Unavailable probes (no
    checkpoint yet, CPU serve not viable) never count as failures."""
    trainer = training_process
    if trainer is None or not _cpu_serving_ok():
        return
    consecutive_fails = 0
    while not stop.wait(SMOKE_POLL_S):
        report = run_smoke_probe("midtrain")
        if report is None:
            continue  # unavailable != failed
        if _probe_passes(report):
            consecutive_fails = 0
            log("smoke gate: probe passed - training continues")
        else:
            consecutive_fails += 1
            log(f"smoke gate: probe FAILED "
                f"({consecutive_fails}/{SMOKE_MAX_CONSECUTIVE_FAILS})")
            if consecutive_fails >= SMOKE_MAX_CONSECUTIVE_FAILS:
                reason = ("smoke gate: 2 consecutive failed probes - "
                          "adapter is broken, aborting training")
                log(reason)
                abort_reason.append(reason)
                trainer.terminate()
                return


def run_training() -> bool:
    global training_process
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
    # Stream the trainer's output to a live log (the old capture-to-buffer
    # died with subprocess.run); on exit the tail goes to last_stage.txt.
    train_log_handle = (REPORTS.parent / "soup_train_live.log").open("w",
                                                                    encoding="utf-8")
    training_process = subprocess.Popen(
        [str(SOUP_EXE), "train", "--config", str(SOUP_CONFIG), "--yes"],
        stdout=train_log_handle, stderr=subprocess.STDOUT,
    )
    stop = threading.Event()
    abort_reason: list[str] = []
    watcher = threading.Thread(target=smoke_watcher,
                               args=(stop, abort_reason), daemon=True)
    watcher.start()
    try:
        returncode = training_process.wait()  # releases the GIL
    finally:
        stop.set()
        watcher.join(timeout=5)
        training_process = None
        try:
            train_log_handle.close()
        except OSError:
            pass
    try:
        live_text = (REPORTS.parent / "soup_train_live.log").read_text(
            encoding="utf-8", errors="replace")
        LOG_TAIL.write_text(live_text[-4000:], encoding="utf-8")
    except OSError:
        pass
    if abort_reason:
        log(f"soup train ABORTED - {abort_reason[0]}")
        return False
    if returncode != 0:
        log(f"soup train FAILED (exit {returncode})")
        return False
    log("soup train finished")
    return True


def write_verdict(baseline: dict | None, tuned: dict | None,
                  reason: str = "") -> str:
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
        *( [f"_Reason: {reason}_", ""] if reason else [] ),
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
        server = None
        wait_vram_clear()
        if not run_training():
            write_verdict(baseline, None)
            return 1
        # Phantom-verdict lesson (2026-09-09): the tuned exam once graded
        # port 20129 AFTER the teacher server was terminated - 26 empty
        # replies became a 0/26 "regression" that never tested the adapter.
        # Serve the tuned adapter FIRST and confirm it answers; on PROMOTE
        # this same server stays up as Aali's live brain
        # (agent_loop.promoted_own_model -> promoted.json base_url).
        adapter = _adapter_dir()
        log(f"serving tuned adapter on :{PORT} ({adapter})")
        promoted_server: subprocess.Popen | None = None
        try:
            promoted_server = subprocess.Popen(
                [str(SOUP_EXE), "serve", "--model", str(adapter), "--port", str(PORT)],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log(f"could not serve tuned adapter ({exc})")
            write_verdict(baseline, None, "tuned adapter server failed to start")
            return 1
        if not _wait_http(f"http://127.0.0.1:{PORT}/v1/models", timeout_s=900,
                          log_fn=log):
            log("tuned adapter server never became ready - refusing to grade a dead endpoint")
            promoted_server.terminate()
            write_verdict(baseline, None, "tuned adapter server never became ready")
            return 1
        report_text = write_verdict(baseline, tuned)
        log("=== soup pipeline done ===")
        if "Verdict: PROMOTE" in report_text:
            log(f"promoted adapter stays serving on :{PORT} "
                f"(pid {promoted_server.pid})")
        else:
            log("no promotion - stopping the tuned-adapter server")
            promoted_server.terminate()
            promoted_server = None
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
