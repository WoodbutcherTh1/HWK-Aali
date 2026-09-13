#!/usr/bin/env python3
"""Watch for the v5 tuned exam report, then emit a per-case breakdown vs v4.

Runs detached (launch_detached.py) alongside the soup pipeline. When
soup_exam_report_tuned.json is rewritten with a NEWER timestamp than the
v4 snapshot, it grades per-case: media family, security family, and the
near-miss-adjacent tool_seen fields, then writes soup_v5_exam_breakdown.log.

Standalone on purpose: never touches the pipeline, the server, or the lock.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

SOUP_DIR = Path(os.environ.get("AALI_SOUP_DIR", "D:/hwk-data/soup"))
SNAPSHOT_DIR = SOUP_DIR / "exam_reports_v4"
TUNED = SOUP_DIR / "soup_exam_report_tuned.json"
LOG = Path(os.environ.get("AALI_BREAKDOWN_LOG", "D:/hwk-data/soup_v5_exam_breakdown.log"))

POLL_SECONDS = float(os.environ.get("AALI_BREAKDOWN_POLL", "60"))
MAX_WAIT_HOURS = float(os.environ.get("AALI_BREAKDOWN_MAX_HOURS", "6.0"))

MEDIA_IDS = {"img_basic_en", "img_basic_ar", "video_ad_followup_en_turn2",
             "video_ad_followup_ar_turn2", "video_plain_rough_en"}
SECURITY_IDS = {"security_refuse_env_dump_en", "security_refuse_env_dump_ar",
                "security_refuse_system_prompt_extraction_en",
                "security_no_save_web_instruction_to_memory_en"}
# Cases where v4 invented plausible-but-fake tool names (math/file/registration/
# final-text-instead-of-memory) — the exact disease the near-miss episodes target.
NEAR_MISS_IDS = {"control_no_tool_math", "control_read_file",
                 "memory_save_user_directive_en", "memory_save_user_directive_ar",
                 "memory_recall_before_denial_en", "memory_contradiction_surfacing_ar",
                 "emoji_react_celebration_ar"}


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_report(path: Path) -> list:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig"):
        try:
            data = json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    rows = data.get("results") if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []


def mtime_of(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def by_id(rows: list) -> dict:
    return {r.get("id"): r for r in rows if isinstance(r, dict) and r.get("id")}


def family_summary(old: dict, new: dict) -> tuple[int, int, int, int]:
    """(v4_pass, v5_pass, total, tool_flips) over the union of ids."""
    ids = sorted(set(old) | set(new))
    p_old = sum(1 for i in ids if old.get(i, {}).get("pass"))
    p_new = sum(1 for i in ids if new.get(i, {}).get("pass"))
    flips = sum(1 for i in ids
                if old.get(i, {}).get("tool_seen") != new.get(i, {}).get("tool_seen"))
    return p_old, p_new, len(ids), flips


def main() -> int:
    baseline_path = SNAPSHOT_DIR / "tuned.v4.json"
    if not TUNED.exists() or not baseline_path.exists():
        log(f"missing inputs: tuned={TUNED.exists()} v4snapshot={baseline_path.exists()}")
        return 2

    v4_rows = load_report(baseline_path)
    v4_mtime = mtime_of(TUNED)
    deadline = time.time() + MAX_WAIT_HOURS * 3600
    log(f"watching {TUNED.name} (v4 mtime {datetime.fromtimestamp(v4_mtime):%H:%M:%S}); "
        f"waiting for the v5 tuned exam to overwrite it")

    last_beat = time.time()
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        if time.time() - last_beat >= 300:  # heartbeat: proves liveness in the log
            log(f"alive, still waiting ({(time.time() - v4_mtime) / 60:.0f} min since v4 mtime)")
            last_beat = time.time()
        mtime = mtime_of(TUNED)
        if mtime <= v4_mtime:
            continue
        # fresh report: wait briefly for the write to complete
        time.sleep(10)
        v5_rows = load_report(TUNED)
        if not v5_rows:
            log("new report unreadable; retrying")
            v4_mtime = mtime
            continue

        old, new = by_id(v4_rows), by_id(v5_rows)
        total_old = sum(1 for r in old.values() if r.get("pass"))
        total_new = sum(1 for r in new.values() if r.get("pass"))

        log("=== v5 TUNED EXAM — PER-CASE BREAKDOWN (vs v4) ===")
        log(f"TOTAL: v4 {total_old}/{len(old)}  ->  v5 {total_new}/{len(new)}")
        for fam_name, ids in (("MEDIA", MEDIA_IDS), ("SECURITY", SECURITY_IDS),
                              ("NEAR-MISS", NEAR_MISS_IDS)):
            fo, fn, tot, fl = family_summary({k: v for k, v in old.items() if k in ids},
                                             {k: v for k, v in new.items() if k in ids})
            marker = "IMPROVED" if fn > fo else ("same" if fn == fo else "REGRESSED")
            log(f"{fam_name}: v4 {fo} -> v5 {fn} of {tot}  (tool-name flips: {fl})  [{marker}]")
            for cid in sorted(ids):
                o, n = old.get(cid, {}), new.get(cid, {})
                if o.get("pass") or n.get("pass") or o.get("tool_seen") != n.get("tool_seen"):
                    log(f"  {cid}: v4 seen={o.get('tool_seen')!r} pass={o.get('pass')} "
                        f"->  v5 seen={n.get('tool_seen')!r} pass={n.get('pass')}")
        log("--- all cases ---")
        for cid in sorted(set(old) | set(new)):
            o, n = old.get(cid, {}), new.get(cid, {})
            if not (o.get("pass") or n.get("pass")) and o.get("tool_seen") == n.get("tool_seen"):
                continue  # unchanged failure, keep the log tight
            log(f"  {cid}: v4 seen={o.get('tool_seen')!r} pass={o.get('pass')} "
                f"->  v5 seen={n.get('tool_seen')!r} pass={n.get('pass')}")
        log("=== end breakdown ===")
        return 0

    log(f"timeout after {MAX_WAIT_HOURS:.0f}h — tuned report never refreshed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
