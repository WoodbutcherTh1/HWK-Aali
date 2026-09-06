import json
import random
import sys
from pathlib import Path

import pandas as pd

REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
RAW = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("D:/hwk-data/raw")

SYSTEM_EN = (
    "You are Aali. For tools reply {\"tool\": name, \"arguments\": {...}}. "
    "For the final answer use {\"tool\": \"final\", \"content\": reply}. "
    "Answer in the user's language and be honest."
)

MAX_CHARS = 2600
PER_SOURCE = 4000
SEED = 1337


def load_alpaca_style(path: Path) -> list:
    if not path.exists():
        return []
    df = pd.read_parquet(path)
    rows = []
    for rec in df.to_dict("records"):
        instruction = str(rec.get("instruction", "")).strip()
        inp = str(rec.get("input", "") or "").strip()
        output = str(rec.get("output", "")).strip()
        if not instruction or not output:
            continue
        user_text = f"{instruction}\n{inp}" if inp else instruction
        if len(user_text) + len(output) > MAX_CHARS:
            continue
        rows.append({"user": user_text, "assistant": output})
    return rows


def to_episode(user_text: str, assistant_text: str) -> dict:
    answer = json.dumps({"tool": "final", "content": assistant_text}, ensure_ascii=False)
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_EN},
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": answer},
        ]
    }


def main() -> None:
    rng = random.Random(SEED)
    out_path = REPO / "data" / "sft_mix.jsonl"

    tool_path = REPO / "data" / "tool_calling_sft.jsonl"
    tool_lines = tool_path.read_text(encoding="utf-8").splitlines() if tool_path.exists() else []
    tool_lines = [line for line in tool_lines if line.strip()]

    general_records = []
    for name in ("alpaca", "alpaca_gpt4", "code_alpaca"):
        rows = load_alpaca_style(RAW / name / "part-00000.parquet")
        rng.shuffle(rows)
        rows = rows[:PER_SOURCE]
        for row in rows:
            general_records.append(to_episode(row["user"], row["assistant"]))
        print(f"[{name}] usable={len(rows)}")

    general_lines = [json.dumps(rec, ensure_ascii=False) for rec in general_records]

    all_lines = tool_lines + general_lines
    rng.shuffle(all_lines)

    out_path.write_text("\n".join(all_lines) + "\n", encoding="utf-8")
    print(f"wrote {len(all_lines)} records ({len(tool_lines)} tool-calling + {len(general_lines)} general) -> {out_path}")


if __name__ == "__main__":
    main()
