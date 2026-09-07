"""Export HWK data for Soup (MakazhanAlpamys/Soup).

Reads data/sft_mix.jsonl (Aali's SFT episodes, messages format) and writes:
  D:/hwk-data/soup/sft_alpaca.jsonl     - alpaca-style records for `soup train`
  D:/hwk-data/soup/exam_prompts.jsonl   - the 12 held-out exam cases as
                                          {id, prompt, ...grading metadata} for
                                          `soup infer` / scripts/soup_exam.py

The system prompt (the {"tool": ...} protocol contract) is PREPENDED to each
instruction so it survives any chat template Soup applies. Runs on CPU in the
project venv; touches nothing on the GPU.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

OUT_DIR = Path("D:/hwk-data/soup")


def _episode_to_alpaca(record: dict) -> dict:
    messages = record["messages"]
    system = ""
    users: list[str] = []
    assistants: list[str] = []
    for message in messages:
        role, content = message.get("role", ""), str(message.get("content", ""))
        if role == "system":
            system = content
        elif role == "user":
            users.append(content)
        elif role == "assistant":
            assistants.append(content)
    instruction = "\n".join(users)
    if system:
        instruction = f"{system}\n\n{instruction}"
    return {
        "instruction": instruction,
        "input": "",
        "output": "\n".join(assistants),
        "system": system,
        "source": "hwk-sft-mix",
    }


def _exam_prompt(case: dict) -> tuple[str, str]:
    """Same prefix shape scripts/exam_tool_calling.py generates for Aali."""
    lines = [f"System: {case['system']}"]
    for role, content in case["turns"]:
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines) + "\nAssistant:", case["id"]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sft_path = ROOT / "data" / "sft_mix.jsonl"
    episodes = [json.loads(line) for line in sft_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    alpaca = [_episode_to_alpaca(record) for record in episodes]
    out_sft = OUT_DIR / "sft_alpaca.jsonl"
    out_sft.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in alpaca) + "\n", encoding="utf-8")
    print(f"sft_alpaca.jsonl: {len(alpaca)} records -> {out_sft}")

    exam_path = ROOT / "data" / "exam_tool_calling.jsonl"
    cases = [json.loads(line) for line in exam_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    exam_rows = []
    for case in cases:
        prompt, case_id = _exam_prompt(case)
        exam_rows.append({
            "id": case_id,
            "prompt": prompt,
            "expect_tool": case.get("expect_tool"),
            "required_args": case.get("required_args", []),
            "require_disclaimer": case.get("require_disclaimer", False),
            "disclaimer_keywords": case.get("disclaimer_keywords", []),
        })
    out_exam = OUT_DIR / "exam_prompts.jsonl"
    out_exam.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in exam_rows) + "\n", encoding="utf-8")
    print(f"exam_prompts.jsonl: {len(exam_rows)} cases -> {out_exam}")

    arabic = sum(1 for r in alpaca if any("\u0600" <= ch <= "\u06ff" for ch in r["instruction"]))
    print(f"note: {arabic} records contain Arabic in the instruction (the re-SFT "
          "rebalance from the recovery report is still on the roadmap)")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
