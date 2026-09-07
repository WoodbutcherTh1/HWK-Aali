"""Run the 12-case Aali tool-calling exam against a Soup-served model.

The Soup server (`soup serve`) exposes an OpenAI-compatible API; this script
replays the exact prompts Aali's exam uses (exported by
scripts/soup_export_sft.py to D:/hwk-data/soup/exam_prompts.jsonl) and grades
them with the same rules as scripts/exam_tool_calling.py, so scores are
directly comparable between Aali and any teacher/comparison model.

Usage:
  1. Start the server (after Phase A is paused, so the GPU is free):
     D:/hwk-tools/soup-venv/Scripts/soup.exe serve --model Qwen/Qwen2.5-1.5B-Instruct --port 20129
  2. Grade it:
     .venv/Scripts/python.exe scripts/soup_exam.py --base-url http://127.0.0.1:20129/v1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EXAM = Path("D:/hwk-data/soup/exam_prompts.jsonl")


def _extract_json(text: str) -> dict | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def grade_case(case: dict, raw_output: str) -> dict:
    parsed = _extract_json(raw_output)
    result = {
        "id": case["id"], "parsed_ok": parsed is not None,
        "tool_correct": False, "args_ok": True, "disclaimer_ok": True, "pass": False,
    }
    if parsed is None:
        return result
    tool = parsed.get("tool")
    result["tool_seen"] = tool
    result["tool_correct"] = tool == case["expect_tool"]
    if case["expect_tool"] != "final":
        args = parsed.get("arguments", {}) if isinstance(parsed.get("arguments"), dict) else {}
        missing = [k for k in case.get("required_args", []) if k not in args]
        result["missing_args"] = missing
        result["args_ok"] = not missing
    if case.get("require_disclaimer") and tool == "final":
        content = str(parsed.get("content", "")).lower()
        keywords = [k.lower() for k in case.get("disclaimer_keywords", [])]
        result["disclaimer_ok"] = any(k in content for k in keywords)
    result["pass"] = result["parsed_ok"] and result["tool_correct"] and result["args_ok"] and result["disclaimer_ok"]
    return result


def ask(base_url: str, model: str, prompt: str, timeout: int = 180) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.0,
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade a Soup-served model on Aali's tool-calling exam")
    parser.add_argument("--base-url", default="http://127.0.0.1:20129/v1")
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--exam", default=str(EXAM))
    parser.add_argument("--output", default="D:/hwk-data/soup_exam_report.json")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    cases = [json.loads(line) for line in Path(args.exam).read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['id']} ...", flush=True)
        try:
            raw = ask(args.base_url, args.model, case["prompt"])
        except Exception as exc:  # noqa: BLE001
            raw = ""
            print(f"    request failed: {exc}", flush=True)
        graded = grade_case(case, raw)
        graded["raw_output"] = raw[:300]
        results.append(graded)
        print(f"    -> {'PASS' if graded['pass'] else 'FAIL'}", flush=True)
        time.sleep(0.3)

    passed = sum(1 for r in results if r["pass"])
    report = {"model": args.model, "endpoint": args.base_url, "score": f"{passed}/{len(results)}", "results": results}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Score: {passed}/{len(results)}  ->  {args.output}")


if __name__ == "__main__":
    main()
