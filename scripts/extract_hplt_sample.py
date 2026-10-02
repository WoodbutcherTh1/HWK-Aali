"""Extract the HPLT v2 Hebrew SAMPLE into the corpus JSONL (Phase 3.5.4).

Owner decision (2026-10-02): keep the SAMPLED 1 GB prefix of the HPLT v2
cleaned heb_Hebr shard only (~507M tokens estimated) — no full 28.8 GB
download. License: CC0 packaging (allowed list; verified live).

Input: the resume-safe prefix at raw/hplt/heb_Hebr_cleaned_1.jsonl.zst
(1,000,000,000 bytes — the start of the real file). Output: one JSONL row
per document {id, url, collection, text} under extracted/hplt/.

Privacy gate (owner rule: no personal data): documents whose `pii` field
lists PII spans are EXCLUDED (1.8% of the sample) and counted. This is a
hard privacy rule, not Phase-4 cleaning. Content-free counters only.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

import zstandard

RAW = Path("D:/hwk-data/hebrew/raw/hplt/heb_Hebr_cleaned_1.jsonl.zst")
OUT = Path("D:/hwk-data/hebrew/extracted/hplt/hplt_sample.jsonl")
EXTRACT_LOG = Path("D:/hwk-data/hebrew/EXTRACT_LOG.md")
SOURCE_URL = "https://data.hplt-project.org/two/cleaned/heb_Hebr/1.jsonl.zst"

HEB = range(0x0590, 0x05FF + 1)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    line = f"[{utcnow()} UTC] {message}"
    print(line, flush=True)


def main() -> None:
    if not RAW.exists():
        raise SystemExit(f"missing {RAW} - run scripts/sample_hplt.py first")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    docs = pii_dropped = empty = bad = 0
    chars_total = hebrew_chars = 0
    with OUT.open("w", encoding="utf-8", newline="\n") as out:
        with RAW.open("rb") as raw_f:
            dctx = zstandard.ZstdDecompressor()
            with dctx.stream_reader(raw_f) as zr:
                text_stream = io.TextIOWrapper(zr, encoding="utf-8", errors="replace")
                for line in text_stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        doc = json.loads(line)
                    except json.JSONDecodeError:
                        bad += 1
                        continue
                    t = (doc.get("text") or "").strip()
                    if not t:
                        empty += 1
                        continue
                    if doc.get("pii"):
                        pii_dropped += 1
                        continue
                    out.write(json.dumps({
                        "id": doc.get("id", ""),
                        "url": doc.get("u", ""),
                        "collection": doc.get("collection", ""),
                        "source_url": SOURCE_URL,
                        "text": t,
                    }, ensure_ascii=False) + "\n")
                    docs += 1
                    chars_total += len(t)
                    hebrew_chars += sum(1 for c in t if ord(c) in HEB)
                    if docs % 50000 == 0:
                        log(f"extract: {docs:,} docs, {chars_total / 1e6:.0f}M chars")
    he_share = hebrew_chars / max(chars_total, 1)
    log(f"extract DONE: docs={docs:,} chars={chars_total:,} hebrew={he_share:.1%} "
        f"pii-dropped={pii_dropped:,} empty={empty:,} bad={bad:,}")
    section = (
        f"\n## HPLT v2 Hebrew SAMPLE extraction ({utcnow()})\n"
        f"- docs written: {docs:,} -> extracted/hplt/hplt_sample.jsonl\n"
        f"- text chars: {chars_total:,} ({chars_total / 1e9:.2f} GB), Hebrew share {he_share:.1%}\n"
        f"- rough token estimate (chars/4): ~{chars_total // 4:,}\n"
        f"- privacy: {pii_dropped:,} docs excluded (pii spans flagged at source); "
        f"empty {empty:,}, bad lines {bad:,}\n"
        f"- scope: SAMPLE ONLY (1 GB prefix of the 28.8 GB shard) per owner decision\n"
    )
    print(section, flush=True)
    try:
        with EXTRACT_LOG.open("a", encoding="utf-8") as f:
            f.write(section)
    except OSError:
        pass


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
