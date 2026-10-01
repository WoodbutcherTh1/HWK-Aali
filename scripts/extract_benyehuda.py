"""Extract Project Ben-Yehuda texts to JSONL (Phase 3.2).

Input: the verified git clone at raw/benyehuda_repo (pseudocatalogue.csv +
txt/<path>.txt, PUBLIC DOMAIN per the repo's own LICENSE). Output: one
JSONL row per work: {id, title, authors, translators, original_language,
genre, text}. Content-free counters to EXTRACT_LOG.md only.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO = Path("D:/hwk-data/hebrew/raw/benyehuda_repo")
OUT = Path("D:/hwk-data/hebrew/extracted/benyehuda/benyehuda_works.jsonl")
LOG = Path("D:/hwk-data/hebrew/EXTRACT_LOG.md")


def log(message: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {message}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def main() -> None:
    catalogue = REPO / "pseudocatalogue.csv"
    if not catalogue.exists():
        raise SystemExit("pseudocatalogue.csv missing - clone first")
    OUT.parent.mkdir(parents=True, exist_ok=True)

    rows_written = 0
    missing_files = 0
    chars_total = 0
    latin_heavy = 0  # original_language heuristics, content-free counters only

    with catalogue.open("r", encoding="utf-8", newline="") as handle, \
            OUT.open("w", encoding="utf-8", newline="\n") as out:
        reader = csv.DictReader(
            handle,
            fieldnames=["_id", "path", "title", "authors", "translators",
                        "original_language", "genre", "source_edition"],
        )
        for row in reader:
            rel = (row.get("path") or "").strip().strip("/")
            if not rel:
                continue
            txt_path = REPO / "txt" / f"{rel}.txt"
            if not txt_path.exists():
                missing_files += 1
                continue
            try:
                text = txt_path.read_text(encoding="utf-8", errors="strict")
            except UnicodeDecodeError:
                missing_files += 1
                continue
            chars_total += len(text)
            # content-free counter: works whose path sits under a
            # latin-original translation tree still count normally
            out.write(json.dumps({
                "id": (row.get("_id") or "").strip(),
                "title": row.get("title", ""),
                "authors": row.get("authors", ""),
                "translators": row.get("translators", ""),
                "original_language": row.get("original_language", ""),
                "genre": row.get("genre", ""),
                "text": text,
            }, ensure_ascii=False) + "\n")
            rows_written += 1
            if rows_written % 5000 == 0:
                log(f"benyehuda: {rows_written} works, {chars_total} chars so far")

    log(f"benyehuda extraction DONE: {rows_written} works, "
        f"{chars_total} chars ({chars_total / 1e9:.2f} GB), "
        f"missing/unreadable files: {missing_files}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
