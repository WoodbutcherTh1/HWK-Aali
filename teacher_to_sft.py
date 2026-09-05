"""Turn transcripts saved by claude_teach.py into SFT instruction records.

Reads D:\\hwk-data\\teacher\\*.jsonl, keeps clean question/answer pairs,
de-duplicates them, and writes them in the messages format that
train_scratch.py's SFT mode consumes (data/teacher_sft.jsonl).

Usage:
  python teacher_to_sft.py
  python teacher_to_sft.py --teacher D:/hwk-data/teacher --out data/teacher_sft.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_TEACHER_DIR = Path("D:/hwk-data/teacher")
DEFAULT_OUT = Path("data/teacher_sft.jsonl")
MIN_REPLY_CHARS = 40


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def convert(teacher_dir: Path, out_path: Path) -> dict[str, int]:
    seen: set[str] = set()
    records: list[dict[str, object]] = []
    sources = 0
    skipped = 0
    for path in sorted(teacher_dir.glob("*.jsonl")):
        if not path.exists():
            continue
        for line_number, line in enumerate(path.open(encoding="utf-8"), 1):
            if not line.strip():
                continue
            sources += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            prompt = record.get("prompt")
            reply = record.get("reply")
            if not isinstance(prompt, str) or not isinstance(reply, str):
                skipped += 1
                continue
            if len(reply.strip()) < MIN_REPLY_CHARS:
                skipped += 1
                continue
            key = hashlib.sha1((prompt + "\x00" + reply).encode("utf-8")).hexdigest()
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            records.append(
                {
                    "messages": [
                        {"role": "user", "content": prompt.strip()},
                        {"role": "assistant", "content": reply.strip()},
                    ]
                }
            )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    stats = {"read": sources, "skipped": skipped, "written": len(records)}
    return stats


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Convert teacher transcripts to SFT data.")
    parser.add_argument("--teacher", default=str(DEFAULT_TEACHER_DIR))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    stats = convert(Path(args.teacher), Path(args.out))
    print(
        f"teacher records read={stats['read']} skipped={stats['skipped']} "
        f"written={stats['written']} -> {args.out}"
    )


if __name__ == "__main__":
    main()
