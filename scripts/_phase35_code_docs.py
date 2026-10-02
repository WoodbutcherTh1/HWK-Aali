"""One-off: append the code-comments source block to SOURCES.md (2026-10-02)."""

from __future__ import annotations

import sys
from pathlib import Path

HEB = Path("D:/hwk-data/hebrew")

BLOCK = """
## 11. Hebrew code comments — MIT/Apache/BSD/CC0/Unlicense repos — FETCHED 2026-10-02
- Method: UNAUTHENTICATED (owner decision). GitHub CODE search requires
  auth (verified live: 401) and even `language:` qualifiers 422 on
  anonymous REST search — so: plain text search q=hebrew (sort=stars,
  5 pages x 100) -> client-side filter language in {Hebrew, JS, TS,
  Python, HTML, CSS, Java, C++, C, PHP, Ruby, Go} -> license gate on the
  API's SPDX metadata {MIT, Apache-2.0, BSD-2/3-Clause, CC0-1.0,
  Unlicense} -> per-repo codeload tarball -> local extraction of Hebrew-
  script COMMENT LINES only (#, //, /* */, <!-- -->) — never string
  literals or UI text.
- Result: 114 repos fetched (0 failures), 3,188 Hebrew comment lines,
  165,714 chars (~41K tokens) -> extracted/code_comments/code_comments.jsonl
- Honest verdict: NEGLIGIBLE yield — Hebrew-speaking developers comment in
  English. Kept (clean + tiny); Phase 5 may drop it if it grades noisy.
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
    append_once(HEB / "SOURCES.md", "## 11. Hebrew code comments", BLOCK)
