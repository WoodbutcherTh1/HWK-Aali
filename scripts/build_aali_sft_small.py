"""Build a SMALL-MODEL-targeted SFT mix from sft_v2.jsonl (Phase C run 6+).

2026-09-14 run-5 lesson: the 110M own-brain trained on the full 5,434-row mix
served real Arabic (1,719 short-Arabic-answer rows carry it) but looped
"emojis:" on English. Mix audit explained both:
  - 3,590 rows (66%) are tool-call JSON targets - a 110M model spends most of
    its gradient imitating JSON boilerplate, and emoji-tool rows make
    "emojis" a high-frequency token (the loop signature);
  - natural ENGLISH answers number ~125 rows (2.3%) - almost no supervision
    for English prose, hence language mixing.
The Soup adapter (1.5B) can eat that mix; the scratch brain cannot.

Recipe (SFT_SMALL_* constants):
  1. Classify each row: TOOL (final assistant turn contains {"tool" ...}) or
     NATURAL, and by language (Arabic-letter count in the user text).
  2. NATURAL short answers (<=NATURAL_MAX_ANSWER_CHARS) are the treasure:
     keep ALL of them; upweight ENGLISH ones xEN_UPWEIGHT (byte-identical
     copies tagged smallmix-upweight#N - train_scratch has no dedup gate,
     the tag preserves provenance in the file).
  3. TOOL rows: keep only the SHORTEST ones (short JSON = cleanest protocol
     signal per token), stratified so the Arabic tool rows survive whole and
     English tool rows fill the remaining budget, longest-first dropped.
  4. Long natural answers (>NATURAL_MAX_ANSWER_CHARS) are dropped: a 110M
     model at ~6 epochs learns short answers well and long answers badly.
Output: D:/hwk-data/soup/sft_small.jsonl + a printed report. Read-only on the
source; never edits rows (only selects / copies / tags).

    .venv/Scripts/python.exe scripts/build_aali_sft_small.py
    # optional: --source ... --target ... --tool-budget 1400 --en-upweight 4
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from pathlib import Path

DEFAULT_SOURCE = "D:/hwk-data/soup/sft_v2.jsonl"
DEFAULT_TARGET = "D:/hwk-data/soup/sft_small.jsonl"

NATURAL_MAX_ANSWER_CHARS = 800
EN_UPWEIGHT = 4
TOOL_BUDGET = 1400          # total tool rows kept (AR tool rows first, then shortest EN)
ARABIC_LETTER_THRESHOLD = 10  # user text with more Arabic letters than this = Arabic row

# ---------------------------------------------------------------- EN seeds
# The mix audit found only THREE natural English answers in 5,434 rows -
# English exists almost solely as tool-call JSON, which is exactly why the
# 110M brain mixes languages (nothing teaches it English prose). These
# authored seeds are the minimum English-natural floor: greetings,
# capability answers, small talk, and final-protocol replies, all short and
# all in the runtime's own voice. They reuse the dataset's system line so
# they stay in-distribution.

_EN_SEEDS: list[tuple[str, str]] = [
    # (user, assistant)
    ("hi", "Hi! I'm Aali, your local assistant. What can I do for you?"),
    ("hello", "Hello! I'm Aali. Ask me anything or give me a task."),
    ("hey there", "Hey! Ready when you are - what do you need?"),
    ("good morning", "Good morning! What are we working on today?"),
    ("how are you", "I'm running well, thanks for asking. How can I help?"),
    ("what can you do", "I can read and edit files, run commands, generate images and videos, transcribe audio, and answer questions - all locally. What do you need?"),
    ("what are your tools", "I have file tools (read, write, edit, list), run_command for shell work, image/video generation, OCR and transcription. Tell me the task and I'll pick the right one."),
    ("who made you", "I'm Aali, built and trained from scratch by team HWK."),
    ("thanks", "Anytime! Anything else you need?"),
    ("thank you", "You're welcome! What's next?"),
    ("can you help me", "Of course. Describe what you need and I'll get started."),
    ("can you run commands", "Yes - I run commands in a sandboxed workspace with an allow-list (python, pip, pytest, node, npm, git). Tell me what to run."),
    ("can you build games", "Yes, I can build apps and games end to end: write the code, install dependencies, run it, and fix issues from the real output."),
    ("list the files here", '{"tool":"list_workspace","arguments":{}}'),
    ("read the file notes.txt", '{"tool":"read_file","arguments":{"path":"notes.txt"}}'),
    ("what does quarterly_report_2024.txt say", '{"tool":"read_file","arguments":{"path":"quarterly_report_2024.txt"}}'),
    ("what does quarterly_report_2024.txt say?", '{"tool":"read_file","arguments":{"path":"quarterly_report_2024.txt"}}'),
    ("quarterly_report_2024.txt", '{"tool":"read_file","arguments":{"path":"quarterly_report_2024.txt"}}'),
    ("make a folder called drafts", '{"tool":"make_directory","arguments":{"path":"drafts"}}'),
    ("draw me a picture of a cat", '{"tool":"generate_image","arguments":{"prompt":"a cat"}}'),
    ("say hello in json", '{"tool":"final","content":"Hello!"}'),
    ("answer with the final format", '{"tool":"final","content":"Understood - this is the final-answer format."}'),
    ("what is 2 plus 2", '{"tool":"final","content":"2 + 2 = 4."}'),
    ("what is your name", '{"tool":"final","content":"My name is Aali."}'),
    ("tell me a fun fact", '{"tool":"final","content":"Honey never spoils - archaeologists have tasted 3,000-year-old honey and it was still good."}'),
]


def _en_seed_records(system_content: str) -> list[dict]:
    return [
        {"messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user},
            {"role": "assistant", "content": answer},
        ], "tag": "smallmix-en-seed"}
        for user, answer in _EN_SEEDS
    ]

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_TOOL_RE = re.compile(r'\{\s*"tool"\s*')


def _final_assistant(record: dict) -> str:
    for message in reversed(record.get("messages", [])):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                return content
    return ""


def _user_text(record: dict) -> str:
    return " ".join(
        message.get("content", "") for message in record.get("messages", [])
        if isinstance(message, dict) and message.get("role") == "user"
    )


def classify(record: dict) -> tuple[str, str]:
    """Return (kind, lang) - kind in {tool, natural}, lang in {ar, en}."""
    answer = _final_assistant(record)
    kind = "tool" if _TOOL_RE.search(answer) else "natural"
    lang = "ar" if len(_ARABIC_RE.findall(_user_text(record))) > ARABIC_LETTER_THRESHOLD else "en"
    return kind, lang


def build(source: Path, target: Path, tool_budget: int, en_upweight: int) -> dict:
    tool_rows: list[tuple[int, dict, str]] = []   # (answer_len, record, lang)
    natural_ar: list[dict] = []
    natural_en: list[dict] = []
    dropped_long = 0
    system_content = ""

    with source.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            for message in record.get("messages", []):
                if isinstance(message, dict) and message.get("role") == "system" \
                        and isinstance(message.get("content"), str):
                    system_content = message["content"]
                    break
            kind, lang = classify(record)
            if kind == "tool":
                tool_rows.append((len(_final_assistant(record)), record, lang))
                continue
            if len(_final_assistant(record)) > NATURAL_MAX_ANSWER_CHARS:
                dropped_long += 1
                continue
            (natural_ar if lang == "ar" else natural_en).append(record)

    # Tool rows: Arabic first (small supply, keep it whole), then the
    # shortest English rows up to the budget. Longer tool JSON adds tokens
    # without adding new protocol shape for a 110M learner.
    tool_ar = sorted([t for t in tool_rows if t[2] == "ar"], key=lambda t: t[0])
    tool_en = sorted([t for t in tool_rows if t[2] == "en"], key=lambda t: t[0])
    kept_tool_ar = [rec for _n, rec, _l in tool_ar]
    remaining = max(0, tool_budget - len(kept_tool_ar))
    kept_tool_en = [rec for _n, rec, _l in tool_en[:remaining]]

    rows: list[dict] = []
    rows.extend(kept_tool_ar)
    rows.extend(kept_tool_en)
    rows.extend(natural_ar)
    en_natural = natural_en + _en_seed_records(system_content)
    for record in en_natural:
        for copy_n in range(1, max(1, en_upweight) + 1):
            tagged = copy.deepcopy(record)
            if copy_n > 1:
                tagged["tag"] = f"smallmix-upweight#{copy_n}"
            rows.append(tagged)

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in rows:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    lang_counts = Counter(classify(record)[1] for record in rows)
    kind_counts = Counter(classify(record)[0] for record in rows)
    report = {
        "source": str(source),
        "target": str(target),
        "source_rows": sum(1 for _ in rows) and (
            len(kept_tool_ar) + len(tool_ar) + len(tool_en) + len(natural_ar)
            + len(natural_en) + dropped_long),
        "written_rows": len(rows),
        "tool_kept": {"ar": len(kept_tool_ar), "en": len(kept_tool_en),
                      "dropped": len(tool_ar) + len(tool_en)
                      - len(kept_tool_ar) - len(kept_tool_en)},
        "natural_kept": {"ar": len(natural_ar), "en": len(natural_en),
                         "en_seeds": len(_EN_SEEDS),
                         "en_upweight": max(1, en_upweight)},
        "dropped_natural_long": dropped_long,
        "written_by_kind": dict(kind_counts),
        "written_by_lang": dict(lang_counts),
        "bytes": target.stat().st_size,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--tool-budget", type=int, default=TOOL_BUDGET)
    parser.add_argument("--en-upweight", type=int, default=EN_UPWEIGHT)
    args = parser.parse_args()

    report = build(Path(args.source), Path(args.target),
                   args.tool_budget, args.en_upweight)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["written_rows"] < 1000:
        print("FAILED: implausibly small mix - refusing to bless it")
        return 1
    print(f"OK: {report['written_rows']} rows -> {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
