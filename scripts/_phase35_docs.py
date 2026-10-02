"""One-off: append Phase 3.5 sections to D:/hwk-data/hebrew docs (2026-10-02).

Appends to LICENSE_REPORT.md and SOURCES.md; idempotent (marker-guarded).
Numbers come from the live probe/fetch runs of fetch_sefaria.py and
fetch_knesset.py. Content-free (no corpus text).
"""

from __future__ import annotations

import sys
from pathlib import Path

HEB = Path("D:/hwk-data/hebrew")

LICENSE_ADDENDUM = """
---

# Phase 3.5 addendum (2026-10-02, live checks — Sefaria + Knesset + academic)

## Sefaria-Export (source 3.5.1)
- Repo: github.com/Sefaria/Sefaria-Export. Its LICENSE.md states there is
  NO overall repo license: "Each text is licensed separately... You can
  find the license for each text in their JSON versions under the
  `license` field. This is generally either 'Public Domain', 'CC0',
  'CC-BY', 'CC-BY-SA' or 'CC-BY-NC'." (verified live 2026-10-02; repo is
  now a lightweight index — the texts live in the public GCS bucket
  gs://sefaria-export/).
- Mechanism: EVERY Hebrew text JSON was Range-probed (first 8 KB; the
  `license` field sits in the file's metadata header) BEFORE any download.
  ALLOWED = Public Domain / PD / CC0 / CC-BY / CC-BY-SA (owner list).
  Everything else was never downloaded.
- Probe results (13,548 Hebrew entries): allowed 6,005 rows
  (Public Domain 5,292 + PD 140 + CC-BY 295 + CC-BY-SA 246 + CC0 32);
  excluded: 6,257 with NO license field (6,214 of them merged.json
  composites — merged versions are skipped entirely for that reason, and
  43 specific versions), 931 "unknown", 354 CC-BY-NC, 1 transient probe
  failure.
- Fetch: one best-licensed version per title -> 5,423 files, 1.90 GB,
  per-file sha256 in raw/sefaria/manifest.jsonl; 1 file skipped (unsafe
  Windows path), 0 download errors.
- VERDICT: READY under per-text license gating (recorded per file in the
  manifest).

## Knesset Corpus (source 3.5.2 — the academic win)
- HaifaCLGroup/KnessetCorpus on HuggingFace: Hebrew parliamentary
  proceedings (plenary 1992-2024, committees 1998-2024; ~35M sentences,
  ~384M+ tokens), curated by University of Haifa (Gili Goldin, Shuly
  Wintner), IAHLT (Nick Howell, Noam Ordan) and TAU-Yaffo (Ella
  Rabinovich). Dataset card license tag: cc-by-sa-4.0 (verified live
  2026-10-02). Paper: arXiv:2405.18115.
- VERDICT: READY. Only the two no-morph sentence subsets were downloaded
  (120 .jsonl.bz2, 3.67 GB, sha256-verified vs LFS oid). Extraction keeps
  ONLY sentence_text + public protocol metadata — no speaker names,
  genders or factions (privacy-first).

## Open-access academic Hebrew — remaining verdicts (3.5.2)
- arXiv: Hebrew-language full text is essentially nonexistent (STEM
  publishes in English; the Hebrew datasets found are news-derived
  (HeSum) or speech (HebDB) — licensing-encumbered / not text). NOT
  VIABLE as a Hebrew source.
- TAU / HUJI / Technion repositories: no license-clean bulk path; TAU
  library terms prohibit systematical downloading, Hebrew OA theses are
  embargoed/mixed. BLOCKED for bulk use.
- IsraParlTweet: rejected (contains tweets — forbidden class).

## Common Crawl class (source 3.5.4) — PENDING OWNER DECISION
- HPLT v2 Hebrew: gated (HTTP 401 without accepting terms) — needs an
  owner account; not fetched.
- HuggingFaceFW/fineweb-2 Hebrew subset: license tag odc-by (Open Data
  Commons Attribution) — NOT in the owner's enumerated allowed list
  (CC0/CC-BY/CC-BY-SA/MIT/Apache-2.0/PD). Flagged for an owner decision
  instead of silently used. It is the pre-built, quality-filtered
  Common-Crawl-class Hebrew corpus (language_score + minhash dedup).
- Raw Common Crawl: still the riskiest path (index queries + WARC fetch +
  language detection); not started per the owner's "do it last" rule.

## Code comments (source 3.5.3) — NOT STARTED (needs owner input)
- GitHub code search API requires an authenticated token; license
  filtering (MIT/Apache/BSD only) plus Hebrew-comment detection over
  permissive repos is only feasible with a token. Expected yield is small
  (Hebrew comments are rare in permissive code). Awaiting owner decision:
  provide a GitHub token, or skip this source.
"""

SOURCES_ADDENDUM = """
## 8. Sefaria-Export — per-text licenses (PD/CC0/CC-BY/CC-BY-SA) — READY (fetched 2026-10-02)
- Repo: github.com/Sefaria/Sefaria-Export (lightweight index); texts in
  the public GCS bucket gs://sefaria-export/ (README verified live).
- Index: books.json (19,754 entries, 13,548 Hebrew), sha256
  dff0dd23027fc9e0b5c992d8b95648b89977cecd31e6e57e21ac382924025edc
- License mechanism: per-text `license` field, Range-probed (head 8 KB)
  for EVERY Hebrew entry before download. Allowed 6,005 rows; excluded
  6,257 missing (merged composites + 43), 931 "unknown", 354 CC-BY-NC,
  1 probe failure — none of the excluded files were downloaded.
- Fetched: 5,423 files / 1.90 GB, one best-licensed version per title,
  per-file sha256 in raw/sefaria/manifest.jsonl
- Credit line: "Sefaria (sefaria.org)" + the per-text license recorded in
  the manifest.
- Extracted: extracted/sefaria/sefaria_versions.jsonl (stats in STATS.md)

## 9. Knesset Corpus — CC BY-SA 4.0 — READY (fetched 2026-10-02)
- huggingface.co/datasets/HaifaCLGroup/KnessetCorpus — curators: University
  of Haifa / IAHLT / TAU-Yaffo; paper arXiv:2405.18115
- License verified live on the dataset card: cc-by-sa-4.0
- Subsets fetched: plenary_no_morph + committee_no_morph sentence shards —
  120 files, 3,670,722,034 bytes, sha256-verified vs LFS oid
  (raw/knesset/manifest.jsonl)
- Privacy: extraction keeps ONLY sentence_text + public protocol metadata
  (no speaker names / genders / factions)
- Quality gate: is_ocr_output sentences skipped (paper documents heavy
  OCR errors in early protocols)
- Extracted: extracted/knesset/knesset_protocols.jsonl (stats in STATS.md)

## 10. Academic Hebrew — remaining verdicts (2026-10-02)
- arXiv: not viable (no Hebrew full-text mass; found datasets are
  news-derived or speech)
- TAU/HUJI/Technion repositories: blocked for bulk (library terms prohibit
  systematic downloading; Hebrew OA theses embargoed/mixed)
- HPLT v2 Hebrew: gated (401) — owner account + terms acceptance needed
- fineweb-2 heb_Hebr: odc-by — flagged for owner decision (not in the
  allowed list); the compliant pre-built Common-Crawl-class alternative
"""


def append_once(path: Path, marker: str, text: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in existing:
        print(f"skip (marker present): {path.name}")
        return
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
    print(f"appended: {path.name}")


def main() -> None:
    append_once(HEB / "LICENSE_REPORT.md", "Phase 3.5 addendum", LICENSE_ADDENDUM)
    append_once(HEB / "SOURCES.md", "## 8. Sefaria-Export", SOURCES_ADDENDUM)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
