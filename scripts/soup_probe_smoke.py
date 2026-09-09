"""CPU-served smoke probe: grade the 6-case subset against a checkpoint.

Runs its own `soup serve --device cpu` on a private port, so it can execute
while the GPU trains - the smoke gate in soup_pipeline.py calls this between
checkpoints. Never touches the GPU. All failure modes exit non-zero (the
caller treats that as 'probe unavailable', not as a failed probe - only a
graded low score counts as a failure).

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
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from soup_exam import SMOKE_CASE_IDS, ask, grade_case, select_cases  # noqa: E402

SOUP_EXE = Path("D:/hwk-tools/soup-venv/Scripts/soup.exe")


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

    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    base_url = f"http://127.0.0.1:{args.port}/v1"

    server = subprocess.Popen(
        [str(SOUP_EXE), "serve", "--model", args.model,
         "--port", str(args.port), "--device", "cpu"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_http(f"{base_url}/models", timeout_s=600):
            print(f"probe server never became ready on :{args.port}", flush=True)
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


if __name__ == "__main__":
    raise SystemExit(main())
