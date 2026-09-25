"""Rebuild the FTS search index from sessions.jsonl (Wave 1 #2).

Usage:
    python scripts/rebuild_search_index.py            # rebuild + stats
    python scripts/rebuild_search_index.py --db PATH  # custom db (tests)

Safe to re-run (idempotent per sid: delete+reinsert). Logs to
D:/hwk-data/search_index.log like every other index operation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "file-agent"))
sys.path.insert(0, str(_REPO))

from file_agent import search_index  # noqa: E402

SESSIONS_FILE = _REPO / "file-agent" / "sessions.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None,
                        help="override the search db path")
    parser.add_argument("--sessions", default=str(SESSIONS_FILE),
                        help="override the sessions.jsonl path")
    args = parser.parse_args()

    sessions: dict[str, dict] = {}
    path = Path(args.sessions)
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        rec = json.loads(line)
                        sessions[rec.get("key") or rec.get("sid")] = rec
                    except json.JSONDecodeError:
                        continue
    total = search_index.backfill(sessions, args.db)
    print(f"reindexed {len(sessions)} sessions, {total} rows -> "
          f"{search_index._db(args.db)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
