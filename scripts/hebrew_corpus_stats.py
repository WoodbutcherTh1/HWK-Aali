"""Quick pre-cleaning stats for extracted Hebrew Wikipedia (Phase 2.3).

Streams every JSON output file, counts articles + Hebrew-char share
(sanity: the corpus must be predominantly Hebrew script), estimates the
token count at chars/4, and appends the results to STATS.md. Content-safe:
numbers only in the log, never page text.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

EXTRACTED = Path("D:/hwk-data/hebrew/extracted/hewiki")
STATS = Path("D:/hwk-data/hebrew/STATS.md")

HEBREW_RE = range(0x0590, 0x05FF + 1)
LATIN_RE = range(0x0041, 0x007A + 1)


def script_share(text: str) -> tuple[int, int, int]:
    hebrew = latin = other = 0
    for ch in text:
        code = ord(ch)
        if code in HEBREW_RE:
            hebrew += 1
        elif code in LATIN_RE:
            latin += 1
        elif not ch.isspace():
            other += 1
    return hebrew, latin, other


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extracted", default=str(EXTRACTED))
    parser.add_argument("--pattern", default="wiki_*",
                        help="glob under --extracted (wiki_* for wikiextractor output, *.jsonl for benyehuda)")
    parser.add_argument("--label", default="Phase 2 — Hebrew Wikipedia extraction stats",
                        help="STATS.md section label")
    args = parser.parse_args()

    articles = 0
    chars_total = 0
    hebrew_chars = 0
    latin_chars = 0
    per_part: dict[str, int] = {}
    for json_file in sorted(Path(args.extracted).rglob(args.pattern)):
        part = json_file.parent.name
        with json_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                articles += 1
                text = record.get("text", "")
                chars_total += len(text)
                hebrew, latin, _other = script_share(text)
                hebrew_chars += hebrew
                latin_chars += latin
                per_part[part] = per_part.get(part, 0) + 1

    hebrew_share = hebrew_chars / max(chars_total, 1)
    token_estimate = chars_total // 4  # rough owner-brief estimate
    verdict = "HEBREW-OK" if hebrew_share >= 0.5 else "NOT-HEBREW-DOMINANT"

    report = [
        "",
        f"## {args.label}",
        f"_generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        f"- articles extracted: **{articles}**",
        f"- extracted text chars: {chars_total} ({chars_total / 1e9:.2f} GB of text)",
        f"- Hebrew-script char share: **{hebrew_share:.1%}** → sanity: {verdict}",
        f"- Latin-script char share: {latin_chars / max(chars_total, 1):.1%} (English names/terms — expected)",
        f"- rough token estimate (chars/4): **~{token_estimate:,}**",
        f"- per-part article counts: {json.dumps(per_part, sort_keys=True)}",
        "- NOTE: pre-cleaning numbers (Phase 4 will remove templates/refs/non-Hebrew).",
    ]
    text = "\n".join(report) + "\n"
    print(text)
    with STATS.open("a", encoding="utf-8") as handle:
        handle.write(text)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
