"""One-off: append the HPLT sample block to STATS.md + SOURCES.md (2026-10-02)."""

from __future__ import annotations

import sys
from pathlib import Path

HEB = Path("D:/hwk-data/hebrew")

STATS_BLOCK = """
## Phase 3.5 — HPLT v2 cleaned heb_Hebr SAMPLE (1 GB prefix = 3.4% of the shard)

_generated 2026-10-02 06:41 UTC_

- sample: first 1,000,000,000 bytes of cleaned/heb_Hebr/1.jsonl.zst
- docs parsed: **539,819**
- text chars: 2,026,839,187 (2.03 GB)
- Hebrew-script char share: **74.4%** → sanity: HEBREW-OK
- language-ID: heb_Hebr on 100% of docs, top-1 prob p10=p50=p90=1.00
- robotstxt=allowed: **100%** (crawl-permission respected at source)
- filter=keep: 100% (cleaned variant), pii-span docs: 1.8% (maskable)
- rough token estimate (chars/4): **~506,709,796** — from 3.4% of the file
- EXTRAPOLATED full shard (~28.8 GB compressed): **~14.6B tokens**
- NOTE: pre-cleaning numbers; license = CC0 packaging (allowed list);
  full download awaits owner approval.
"""

SOURCES_BLOCK = """
### HPLT v2 cleaned heb_Hebr — SAMPLED 2026-10-02 (license verified FIRST)
- data.hplt-project.org/two/cleaned/heb_Hebr/1.jsonl.zst (28,787,468,697
  bytes; md5 d2197ac088e6def0305a950168ee508a published alongside)
- License: packaging CC0 per the HPLT v2.0 Terms of Use ("We license the
  actual packaging of these text data under the Creative Commons CC0
  license"), verified live on hplt-project.org/datasets/v2.0 — CC0 is in
  the owner's allowed list. Source: CC/Internet Archive, Trafilatura
  extraction, OpenLID language-ID, dedup + cleaning by HPLT.
- Sampled: 1 GB prefix (resume-safe; raw/hplt/) = 539,819 docs, 2.03 GB
  chars, 74.4% Hebrew, ~507M tokens; full-shard extrapolation ~14.6B.
- Status: AWAITING OWNER GO for the full 28.8 GB download (Phase 3.5.4).
"""


def append_once(path: Path, marker: str, text: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in existing:
        print(f"skip: {path.name}")
        return
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
    print(f"appended: {path.name}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    append_once(HEB / "STATS.md", "HPLT v2 cleaned heb_Hebr SAMPLE", STATS_BLOCK)
    append_once(HEB / "SOURCES.md", "HPLT v2 cleaned heb_Hebr", SOURCES_BLOCK)
