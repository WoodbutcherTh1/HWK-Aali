# Mentor learning loop — teach Aali from senior-agent sessions (incl. failures)

Owner request (2026-09-06): capture my (the mentor's) conversations with the
user — inputs, outputs, AND the behind-the-scenes process — so Aali can learn
where the mentor **failed** and avoid the same mistakes.

## The pipeline

```
~/.mentor-capture/projects/**/*.jsonl     (the external coding mentor + Freebuff session logs:
  user asks / assistant moves /      every tool call + its result, errors included)
  tool calls & results)
        │  scripts/mentor_capture.py
        ▼
D:/hwk-data/mentor/episodes.jsonl  (Aali's native messages dialect, tokenizable:
  user ask -> {"tool":...} -> "Tool result: {...}" -> ... -> {"tool":"final"})
        │  teacher_to_sft.py-style dedupe + mix into SFT at re-SFT time
        ▼
Phase B SFT (rebalanced mix) → exam gate (now 16 cases incl. anti-hallucination)
```

## What each episode contains

- The user's real request (Arabic or English, scrubbed of secrets).
- The mentor's actual tool sequence: `{"tool": "Bash", "arguments": ...}` etc.
- The raw tool results — **including errors** — as `Tool result: {"ok": false,...}`.
- The final answer, only if the session reached one (`meta.excerpt` marks
  mid-session chunks that didn't).
- `meta` per episode: `has_failure`, `n_failures` (with tool + error text),
  `n_tool_calls`, `chunk`, `session`, `key` (dedupe hash).

The model therefore sees *failure → next move* pairs, not just successes.
That's the lesson: the wrong first command, the error, the recovery.

## Current stats (first run)

| metric | value |
|---|---|
| transcript files | 7 |
| episodes | 12 (10 clean, 2 with failure-recovery arcs) |
| tool calls captured | 140 |
| example lesson learned | mentor ran a Windows command that exited 49 ("Python was not found…"), then re-checked and recovered — Aali sees the whole arc |

## Anti-hallucination exam (extended to 16 cases)

4 new cases gate the re-SFT: `halluc_missing_file_en/ar` (must `read_file`
before describing a file — checking beats guessing) and
`halluc_admit_failure_en/ar` (after a real "file not found" error, the final
answer must ADMIT it, in the user's language, never invent content).
Score with the usual `scripts/exam_tool_calling.py` — the file-not-found
keyword list is graded in both languages.

## Rules baked into the capture

- Secrets scrubbed (API keys, AWS/GitHub tokens, auth headers → `[REDACTED]`).
- Long sessions are **chunked, never truncated** — a failure deep in a
  157-turn session still lands in an episode (that was a real bug, fixed).
- Only genuine human turns start episodes; injected tool results and meta
  entries never become fake "user asks".
- Deduplication by (request, final answer) hash — no double-counting.
- Data lives on `D:/hwk-data/mentor/`, never in the repo (AGENTS.md rule).

## Usage

```bash
# capture now (and any time; it only ever adds new sessions)
.venv/Scripts/python.exe scripts/mentor_capture.py
# stats without writing
.venv/Scripts/python.exe scripts/mentor_capture.py --stats-only
```

At re-SFT time: merge `episodes.jsonl` (with `has_failure` episodes upweighted
~3x) into the rebalanced mix, then the 16-case exam decides promotion — same
gate as everything else.
