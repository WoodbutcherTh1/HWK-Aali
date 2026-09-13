"""Serving-side tool-name validator: invented names never reach the tools.

2026-09-13 (v4/v5 graduation evidence): the 1.5B brain invents plausible
tool names that are NOT in Aali's registry — `paint`, `local_clip`,
`audio_recorder`, `file`, `math`, `registration`, `print`, `run`. Training
levers failed to fix it (x3 upweight: v4 0/5 media; 40 contrastive
near-miss episodes: v5 0/5 media, 0/7 near-miss, zero tool-name flips), so
the correction moves to RUNTIME, at the one choke point every brain path
shares: the tool dispatch in agent_loop.

Design:
- REAL registry names are read live from file_tools._FUNCTIONS every call
  (no cache, so registry changes and test patches are always honored).
- Known inventions map through ALIASES (`paint` -> `generate_image`, ...).
- Unknown names get ONE conservative fuzzy candidate (difflib >= 0.72);
  a second close candidate means ambiguity -> reject with alternatives.
- Rejection returns a teaching tool-result (like the zero-byte write
  guard): the model is told the real names and can retry in-turn.
- Kill switch: AALI_TOOL_GUARD_OFF=1 disables everything (fail-open).
- Audit: best-effort JSONL, content-free (names only, never arguments —
  arguments may carry user content or secrets).
"""
from __future__ import annotations

import difflib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from file_agent import file_tools

FUZZY_CUTOFF = 0.72

# Invented name -> real registry tool. Keys are lowercase; looked up after
# case-folding. Only names actually observed in exams/chat go here — this is
# a correction table, not a license to guess.
ALIASES: dict[str, str] = {
    # image family
    "paint": "generate_image",
    "draw": "generate_image",
    "drawing_tool": "generate_image",
    "image_editor": "edit_image",
    "edit_photo": "edit_image",
    "ocr": "read_image",
    "text_reader": "read_image",
    # video family
    "local_clip": "generate_video",
    "video_quality": "generate_video",
    "video": "generate_video",
    "make_video": "generate_video",
    "audio_recorder": "generate_video",
    # emoji
    "sticker_maker": "generate_emoji",
    "emoji_maker": "generate_emoji",
    # files
    "file": "read_file",
    "read_files": "read_file",
    "cat": "read_file",
    "find": "search_files",
    # misc observed inventions
    "print": "read_document",
    "note": "memory",
    "remember": "memory",
    # command-running inventions (security_refuse_env_dump exams saw 'run'
    # and 'python' called as tools). Renaming is safe: the policy gate still
    # evaluates the corrected run_command call afterward.
    "run": "run_command",
    "python": "run_command",
    "bash": "run_command",
    "shell": "run_command",
}


def _audit(event: dict[str, Any]) -> None:
    """Best-effort, content-free audit line. Never raises."""
    path = os.getenv("AALI_TOOL_GUARD_AUDIT", "D:/hwk-data/tool_guard_audit.jsonl")
    try:
        event = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **event}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def registry_names() -> set[str]:
    """Live registry names — no cache, honors runtime and test changes."""
    return set(file_tools._FUNCTIONS)


def _fuzzy_candidates(name: str) -> list[str]:
    return difflib.get_close_matches(
        name, registry_names(), n=2, cutoff=FUZZY_CUTOFF)


def validate_tool_name(name: str) -> tuple[str, dict[str, Any] | None]:
    """Validate one tool name. Returns (effective_name, correction).

    effective_name: the registry name to execute, "" when rejected.
    correction: None when the name was already valid; otherwise
        {"invented", "resolved" (str | None), "method", "alternatives",
         "rejected": bool} — for logging/teaching only, never executed.
    """
    if os.getenv("AALI_TOOL_GUARD_OFF") == "1":
        return name, None
    known = registry_names()
    if name in known:
        return name, None

    correction: dict[str, Any] = {
        "invented": name, "resolved": None, "method": None,
        "alternatives": [], "rejected": False,
    }
    folded = name.strip().lower()
    if folded in known:  # case/space slip on a real name
        correction.update(resolved=folded, method="casefold")
        _audit({"invented": name, "resolved": folded, "method": "casefold"})
        return folded, correction
    if folded in ALIASES and ALIASES[folded] in known:
        resolved = ALIASES[folded]
        correction.update(resolved=resolved, method="alias")
        _audit({"invented": name, "resolved": resolved, "method": "alias"})
        return resolved, correction

    close = _fuzzy_candidates(name)
    if len(close) == 1:  # one clear near-match: correct conservatively
        resolved = close[0]
        correction.update(resolved=resolved, method="fuzzy")
        _audit({"invented": name, "resolved": resolved, "method": "fuzzy"})
        return resolved, correction

    correction.update(rejected=True, alternatives=close)
    _audit({"invented": name, "resolved": None, "method": "reject",
            "alternatives": close})
    return "", correction


def rejected_result(correction: dict[str, Any]) -> dict[str, Any]:
    """Teaching tool-result for a rejected name (never executed)."""
    invented = correction.get("invented", "?")
    alts = correction.get("alternatives") or []
    hint = f" Did you mean: {', '.join(alts)}?" if alts else ""
    return {
        "ok": False,
        "error": {
            "type": "unknown_tool",
            "message": f"Unknown tool '{invented}'.{hint} Use one of Aali's "
                       "real tools (see the tools list) and try again.",
            "alternatives": alts,
        },
    }
