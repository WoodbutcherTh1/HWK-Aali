"""Held-out exam for Aali's tool-calling / honesty behaviour.

Every case in data/exam_tool_calling.jsonl uses wording that never appears
in data/tool_calling_sft.jsonl or data/sft_mix.jsonl - this measures
generalization, not memorization of the training set.

For each case: replay the given conversation turns, generate the model's
next assistant turn, and check:
  - does it parse as the {"tool": ..., ...} protocol at all
  - did it pick the expected tool (or "final" when no tool was expected)
  - are the required arguments present, for a tool call
  - for cases flagged require_disclaimer, does the final answer contain at
    least one of the given honesty-signal keywords (rough heuristic, not
    proof of quality - always read the full transcript too)

Usage:
    python scripts/exam_tool_calling.py --checkpoint D:/hwk-models/sft-now/checkpoint.pt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_PACKAGE = Path(__file__).resolve().parents[1] / "file-agent"
if str(PROJECT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE))

import torch

from hwk_model import ByteTokenizer, load_checkpoint, load_bpe
from hwk_model.generation import generate_text


def _message_text_prefix(system: str, turns: list[list[str]]) -> str:
    lines = [f"System: {system}"]
    for role, content in turns:
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines) + "\nAssistant:"


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
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None


def grade_case(case: dict, raw_output: str) -> dict:
    parsed = _extract_json(raw_output)
    result = {
        "id": case["id"], "raw_output": raw_output[:400], "parsed_ok": parsed is not None,
        "tool_correct": False, "args_ok": True, "disclaimer_ok": True, "pass": False,
    }
    if parsed is None:
        result["pass"] = False
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Held-out exam for Aali's tool-calling behaviour")
    parser.add_argument("--checkpoint", default="D:/hwk-models/sft-now/checkpoint.pt")
    parser.add_argument("--exam", default="data/exam_tool_calling.jsonl")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--max-new-tokens", type=int, default=200)
    parser.add_argument("--output-json", default="D:/hwk-data/exam_report.json")
    parser.add_argument("--output-txt", default="D:/hwk-data/exam_report.txt")
    args = parser.parse_args()

    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available() else args.device)
    model, payload = load_checkpoint(args.checkpoint, device=device)
    model.eval()

    tokenizer_meta = payload.get("tokenizer") or {}
    if tokenizer_meta.get("type") == "bpe" and tokenizer_meta.get("model_path"):
        tokenizer = load_bpe(tokenizer_meta["model_path"])
    else:
        tokenizer = ByteTokenizer()

    with open(args.exam, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]

    results = []
    for case in cases:
        prompt = _message_text_prefix(case["system"], case["turns"])
        raw = generate_text(model, tokenizer, prompt, max_new_tokens=args.max_new_tokens, temperature=0.0)
        results.append(grade_case(case, raw))

    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    report = {
        "checkpoint": args.checkpoint,
        "checkpoint_step": payload.get("step", 0),
        "score": f"{passed}/{total}",
        "results": results,
    }
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"Aali tool-calling exam - checkpoint {args.checkpoint} (step {payload.get('step', 0)})",
        f"Score: {passed}/{total}",
        "",
    ]
    for r in results:
        mark = "PASS" if r["pass"] else "FAIL"
        lines.append(f"[{mark}] {r['id']}")
        if not r["pass"]:
            lines.append(f"    tool_seen={r.get('tool_seen')} missing_args={r.get('missing_args')} disclaimer_ok={r.get('disclaimer_ok')}")
            lines.append(f"    raw: {r['raw_output']!r}")
    Path(args.output_txt).write_text("\n".join(lines), encoding="utf-8")

    print(f"Score: {passed}/{total}")
    print(f"Full report: {args.output_txt}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
