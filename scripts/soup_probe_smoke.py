"""CPU-served smoke probe: grade the 6-case subset against a checkpoint.

Runs its own `soup serve --device cpu` on a private port, so it can execute
while the GPU trains - the smoke gate in soup_pipeline.py calls this between
checkpoints. Never touches the GPU. All failure modes exit non-zero (the
caller treats that as 'probe unavailable', not as a failed probe - only a
graded low score counts as a failure).

Exit codes:
  0 - probe ran and graded (any behavioral score)
  3 - server never became ready (the server's output tail is printed; also
      captured in D:/hwk-data/smoke_server.log)
  4 - not enough free RAM to even try (pre-flight gate; soup_pipeline skips
      these cycles without burning the ~11 min server timeout)

RAM lesson (2026-09-12 morning): the first mid-train probe worked, every
later one died with "probe server never became ready". Cause: the GPU
trainer's host-side memory grew until only ~0.3 GB of 16 GB was free - the
CPU serve needs a few GB to load Qwen-1.5B and thrashed into the pagefile
forever. The probe now fails fast (exit 4) when free RAM is below
SMOKE_MIN_FREE_RAM_GB, and always captures the server's output so a slow
or dying server shows WHY instead of a bare timeout.

Console kill lesson (2026-09-12 12:20): a probe died 0xC000013A (console
Ctrl+C/close) with an empty tail - killed from outside its own logic. The
server is now spawned with CREATE_NO_WINDOW so it has no console to
receive those events (DEVNULL alone does not detach the console).

Usage:
  python scripts/soup_probe_smoke.py --model D:/hwk-data/soup/tuned/checkpoint-1442 \
      --port 20130 --ids img_basic_en,security_refuse_env_dump_en \
      --output D:/hwk-data/soup/smoke_midtrain.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from soup_exam import SMOKE_CASE_IDS, ask, grade_case, select_cases  # noqa: E402

SOUP_EXE = Path("D:/hwk-tools/soup-venv/Scripts/soup.exe")
SERVER_LOG = Path("D:/hwk-data/smoke_server.log")
# Qwen-1.5B weights + torch runtime on CPU need roughly 3-4 GB; below this
# the server load turns into pagefile thrash that never becomes ready.
SMOKE_MIN_FREE_RAM_GB = 2.0


def _free_ram_gb() -> float | None:
    """Free physical RAM in GB, or None when it cannot be measured.

    Prefers psutil (already an optional dep of soup_pipeline); falls back to
    the Win32 GlobalMemoryStatusEx call so a bare python still works on
    Windows. Unknown RAM never blocks the probe (fail-open)."""
    try:
        import psutil  # type: ignore

        return psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - measurement is best-effort
        pass
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemStatus()
        status.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined]
                ctypes.byref(status)):
            return status.ullAvailPhys / (1024 ** 3)
    except Exception:  # noqa: BLE001 - non-Windows / measurement failed
        pass
    return None


def wait_http(url: str, timeout_s: int = 600) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(5)
    return False


def _dump_server_tail(reason: str) -> None:
    """Echo the server's own last lines so exit 3 is diagnosable."""
    try:
        tail = SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-1500:]
    except OSError:
        tail = "(no server log written)"
    print(reason, flush=True)
    print("--- soup serve (cpu) tail ---", flush=True)
    print(tail, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="CPU smoke probe for soup adapters")
    parser.add_argument("--model", required=True, help="adapter checkpoint dir to serve")
    parser.add_argument("--port", type=int, default=20130)
    parser.add_argument("--ids", default=",".join(SMOKE_CASE_IDS))
    parser.add_argument("--output", default="D:/hwk-data/soup/smoke.json")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    # Pre-flight: without enough free RAM the CPU serve can never load the
    # model (2026-09-12: 0.3 GB free -> 600s of thrash -> exit 3 with no
    # clue). Fail in one second with the number instead.
    free_gb = _free_ram_gb()
    if free_gb is not None and free_gb < SMOKE_MIN_FREE_RAM_GB:
        print(f"probe skipped: only {free_gb:.2f} GB RAM free "
              f"(need >= {SMOKE_MIN_FREE_RAM_GB:.1f} GB for a CPU serve) "
              f"- the trainer holds the rest", flush=True)
        return 4

    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    base_url = f"http://127.0.0.1:{args.port}/v1"

    # Capture the server's output: DEVNULL turned "never became ready" into
    # an undiagnosable mystery - the real error sat in the pipe nobody read.
    try:
        server_log_handle = SERVER_LOG.open("a", encoding="utf-8")
    except OSError:
        server_log_handle = None
    server = subprocess.Popen(
        [str(SOUP_EXE), "serve", "--model", args.model,
         "--port", str(args.port), "--device", "cpu"],
        stdout=server_log_handle or subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,  # no console -> no Ctrl+C/close kill (0xC000013A)
    )
    try:
        if not wait_http(f"{base_url}/models", timeout_s=600):
            _dump_server_tail(f"probe server never became ready on :{args.port}")
            return 3
        cases = select_cases(
            [json.loads(line) for line
             in Path("D:/hwk-data/soup/exam_prompts.jsonl")
             .read_text(encoding="utf-8").splitlines() if line.strip()],
            ids,
        )
        results = []
        for index, case in enumerate(cases, 1):
            print(f"[{index}/{len(cases)}] {case['id']} ...", flush=True)
            raw = ""
            try:
                raw = ask(base_url, args.model, case["prompt"], timeout=300)
            except Exception as exc:  # noqa: BLE001
                print(f"    request failed: {exc}", flush=True)
            graded = grade_case(case, raw)
            graded["raw_output"] = raw[:300]
            results.append(graded)
            print(f"    -> {'PASS' if graded['pass'] else 'FAIL'}", flush=True)
        passed = sum(1 for r in results if r["pass"])
        report = {"model": args.model, "endpoint": base_url, "device": "cpu",
                  "score": f"{passed}/{len(results)}", "results": results}
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Score: {passed}/{len(results)}  ->  {args.output}", flush=True)
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        if server_log_handle is not None:
            server_log_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
