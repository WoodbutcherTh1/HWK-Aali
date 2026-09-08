"""Mentor capture: turn senior coding-agent session logs into SFT episodes
Aali can learn from - INCLUDING the failures.

The owner's request (2026-09-06): capture what the user asked, what the mentor
answered, and the behind-the-scenes process (tool calls + results), so Aali
can learn where the mentor FAILED and avoid the same mistakes.

Input : the mentor session transcripts (JSONL),
        (all sessions). Structure per line:
          {"type":"user","message":{"role":"user","content":str|[blocks]}}
          {"type":"assistant","message":{"content":[{type:"text"|"tool_use",...}]}}
          tool results arrive as user entries with tool_result blocks.
Output: D:/hwk-data/mentor/episodes.jsonl in Aali's native messages dialect:
          user ask -> assistant {"tool":...} -> user "Tool result: {...}" ->
          ... -> assistant {"tool":"final","content":...}
        plus a `meta` object per episode (has_failure, failures, n_tool_calls)
        so training mixes can upweight failure-recovery arcs. Everything is
        secret-scrubbed and length-capped.

Usage:
  python scripts/mentor_capture.py                 # full pass
  python scripts/mentor_capture.py --stats-only    # just report what's there
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"
DEFAULT_OUT = Path("D:/hwk-data/mentor/episodes.jsonl")

SYSTEM_PROMPT = (
    "You are Aali. For tools reply {\"tool\": name, \"arguments\": {...}}. "
    "For the final answer use {\"tool\": \"final\", \"content\": reply}. "
    "Answer in the user's language and be honest. "
    "If a tool returns an error, never claim success: read the error, fix the "
    "approach, retry or honestly report failure."
)

MAX_TURN_CHARS = 3_000
MAX_TURNS_PER_EPISODE = 36  # per chunk; long sessions are CHUNKED, never truncated
MAX_TOOL_RESULT_CHARS = 1_200

SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[REDACTED-KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED-AWS]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "[REDACTED-GH]"),
    (re.compile(r"(?i)(authorization|api[_-]?key|token)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._-]{16,}"),
     "[REDACTED]"),
]


def _scrub(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _clip(text: str, limit: int) -> str:
    text = _scrub(text).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + " …[truncated]"


def _content_text(content: object) -> str:
    """Flatten a mentor-session message content (string or block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_result":
                    inner = block.get("content")
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, list):
                        parts.extend(
                            str(item.get("text", ""))
                            for item in inner if isinstance(item, dict) and item.get("type") == "text"
                        )
        return "\n".join(p for p in parts if p)
    return ""


def _is_error_flag(block: dict) -> bool:
    """Normalize the tool_result error flag: the transcripts carry BOTH real
    booleans and string 'true'/'False' variants - bool('False') is True in
    Python, which would misclassify clean results as failures."""
    raw = block.get("is_error")
    return raw is True or (isinstance(raw, str) and raw.strip().lower() == "true")


def _is_human_user(entry: dict) -> bool:
    """True for genuine human requests, not injected tool results / meta turns."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, list):
        return not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    origin = entry.get("origin") or {}
    return origin.get("kind") == "human"


def parse_session(path: Path) -> list[list[dict]]:
    """Split one transcript file into human-prompt-delimited segments of
    normalized turns: {kind: 'user'|'assistant_text'|'tool_call'|'tool_result', ...}."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") in ("user", "assistant"):
            entries.append(entry)

    segments: list[list[dict]] = []
    current: list[dict] = []
    pending_calls: dict[str, str] = {}  # tool_use_id -> tool name
    for entry in entries:
        role = entry.get("type")
        message = entry.get("message", {})
        content = message.get("content")
        if role == "user":
            blocks = content if isinstance(content, list) else []
            tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if tool_results:
                for block in tool_results:
                    name = pending_calls.pop(str(block.get("tool_use_id")), "unknown_tool")
                    current.append({
                        "kind": "tool_result", "tool": name,
                        "ok": not _is_error_flag(block),
                        "text": _content_text([block]),
                    })
                continue
            if _is_human_user(entry):
                if current:
                    segments.append(current)
                current = []
                pending_calls.clear()
                text = _content_text(content)
                if text.strip():
                    current.append({"kind": "user", "text": text})
            continue
        # assistant entry
        if not isinstance(content, list):
            continue
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                pending_calls[str(block.get("id"))] = str(block.get("name", "unknown_tool"))
                current.append({
                    "kind": "tool_call", "tool": str(block.get("name", "unknown_tool")),
                    "arguments": block.get("arguments") if isinstance(block.get("arguments"), dict) else {},
                })
        if texts and not any(b.get("type") == "tool_use" for b in content if isinstance(b, dict)):
            joined = "\n".join(t for t in texts if t.strip())
            if joined.strip():
                current.append({"kind": "assistant_text", "text": joined})
    if current:
        segments.append(current)
    return segments


def segment_to_episodes(segment: list[dict], session_id: str) -> list[dict]:
    """Convert one normalized segment into one or more Aali-dialect episodes.

    Long sessions are split into consecutive chunks that all carry the original
    user request as context - otherwise a failure deep in a 150-turn session
    would be cut off by any per-episode limit and Aali would never see it."""
    if not segment or segment[0].get("kind") != "user":
        return []
    user_text = _clip(str(segment[0].get("text", "")), MAX_TURN_CHARS)
    system_message = {"role": "system", "content": SYSTEM_PROMPT}
    seed_user = {"role": "user", "content": user_text}

    episodes: list[dict] = []
    messages: list[dict[str, str]] = [system_message, seed_user]
    failures: list[dict] = []
    n_tool_calls = 0
    final_texts: list[str] = []
    turns = 0

    def close_chunk(is_last: bool) -> None:
        nonlocal messages, failures, n_tool_calls, final_texts, turns
        if turns == 0:
            return
        final = "\n".join(final_texts).strip()
        if final:
            messages.append({"role": "assistant", "content": json.dumps(
                {"tool": "final", "content": final[:MAX_TURN_CHARS]}, ensure_ascii=False)})
        if len(messages) < 4:  # system + user + at least one process turn
            return
        chunk_index = len(episodes)
        episode_key = hashlib.sha1(
            user_text.encode("utf-8") + b"\x00" + str(chunk_index).encode()
            + b"\x00" + final[:200].encode("utf-8")
        ).hexdigest()
        episodes.append({
            "messages": [dict(m) for m in messages],
            "meta": {
                "source": "mentor-capture",
                "session": session_id,
                "key": episode_key,
                "chunk": chunk_index,
                "excerpt": not bool(final),
                "has_failure": bool(failures),
                "n_failures": len(failures),
                "failures": failures[:5],
                "n_tool_calls": n_tool_calls,
            },
        })
        messages = [system_message, seed_user]
        failures = []
        n_tool_calls = 0
        final_texts = []
        turns = 0

    for turn in segment[1:]:
        kind = turn["kind"]
        if kind == "user":
            text = _clip(str(turn.get("text", "")), MAX_TURN_CHARS)
            if not text:
                continue
            close_chunk(False)  # a new human request starts a new episode
            messages.append({"role": "user", "content": text})
            turns += 1
        elif kind == "tool_call":
            n_tool_calls += 1
            call = json.dumps(
                {"tool": turn["tool"], "arguments": _scrub(json.dumps(turn.get("arguments", {}), ensure_ascii=False))},
                ensure_ascii=False,
            )
            messages.append({"role": "assistant", "content": _clip(call, MAX_TURN_CHARS)})
            turns += 1
        elif kind == "tool_result":
            result = json.dumps(
                {"ok": bool(turn.get("ok")), "output": _clip(str(turn.get("text", "")), MAX_TOOL_RESULT_CHARS)},
                ensure_ascii=False,
            )
            if not turn.get("ok"):
                failures.append({"tool": turn.get("tool"), "error": _clip(str(turn.get("text", "")), 200)})
            messages.append({"role": "user", "content": _clip("Tool result: " + result, MAX_TOOL_RESULT_CHARS + 20)})
            turns += 1
        elif kind == "assistant_text":
            final_texts.append(_clip(str(turn.get("text", "")), MAX_TURN_CHARS))
        if turns >= MAX_TURNS_PER_EPISODE:
            close_chunk(False)
    close_chunk(True)
    return episodes


def collect(projects_dir: Path, out_path: Path, stats_only: bool = False) -> dict:
    files = sorted(projects_dir.glob("*/*.jsonl"))
    episodes: list[dict] = []
    seen: set[str] = set()
    files_read = segments_total = 0
    for path in files:
        files_read += 1
        for segment in parse_session(path):
            segments_total += 1
            for episode in segment_to_episodes(segment, path.stem):
                key = episode["meta"]["key"]
                if key in seen:
                    continue
                seen.add(key)
                episodes.append(episode)

    with_failure = sum(1 for e in episodes if e["meta"]["has_failure"])
    stats = {
        "transcript_files": files_read,
        "segments": segments_total,
        "episodes": len(episodes),
        "with_failures": with_failure,
        "clean": len(episodes) - with_failure,
        "tool_calls_total": sum(e["meta"]["n_tool_calls"] for e in episodes),
    }
    if not stats_only and episodes:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            for episode in episodes:
                handle.write(json.dumps(episode, ensure_ascii=False) + "\n")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture mentor sessions as Aali training episodes.")
    parser.add_argument("--projects", default=str(DEFAULT_PROJECTS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    stats = collect(Path(args.projects), Path(args.out), args.stats_only)
    print(json.dumps(stats, indent=2))
    if not args.stats_only:
        print(f"episodes -> {args.out}")


if __name__ == "__main__":
    main()
