"""Overnight Aali-vs-Claude arena — same tasks to both agents, all night.

The owner's order (2026-09-07, before sleep): "Let Aali talk and act like
Claude Code... test on both and don't let go until Aali be like Claude."

Per task, every round:
  1. AALI  — the real server (port 5055, CPU brain while the GPU re-SFTs),
     via /api/ask/stream so tool events are captured too. Artifacts are then
     VERIFIED on disk in the agent workspace (evidence, never claims).
  2. CLAUDE — the free OmniRoute gateway (model "auto"), prompted as the same
     agent; its full tool plan + answer is captured as the reference behavior.

Scoring per task: artifact_verified (Aali), tools_used (Aali, from the live
event stream), reference_tools (Claude). Where Aali falls short of the
reference, a gap episode is written in messages format for the next SFT.

Outputs (all under D:/hwk-data/arena/):
  results.jsonl      one record per task-run (append-only, resume-safe)
  gap_episodes.jsonl messages-format episodes for the next SFT build
  ARENA_REPORT.md    rewritten each round — the morning report

Runs until DAWN (local) or MAX_ROUNDS, whichever first.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ARENA = Path("D:/hwk-data/arena")
RESULTS = ARENA / "results.jsonl"
GAPS = ARENA / "gap_episodes.jsonl"
REPORT = ARENA / "ARENA_REPORT.md"
WORKSPACE = Path(__file__).resolve().parents[1] / "file-agent" / "agent_workspace"

AALI = "http://127.0.0.1:5055"
AALI_KEY = (Path("D:/hwk-data/aali_server_key.txt")).read_text(encoding="utf-8").strip()
GATEWAY = "http://localhost:20128/v1/chat/completions"

DAWN_HOUR = 7          # stop starting new rounds at 07:00 local
MAX_ROUNDS = 14
SETTLE_SECONDS = 20

SYSTEM_CLAUDE = (
    "You are Aali, a local coding agent on the owner's Windows PC. You work by "
    "emitting tool calls. Available tools: write_file(path, content), "
    "read_file(path), append_file(path, content), list_files(path), "
    "search_files(query), run_command(command), fetch_url(url), "
    "generate_emoji(emotion, output). A tool call is a single JSON object on "
    "its own line: {\"tool\": \"...\", \"arguments\": {...}}. For each task, "
    "output the exact sequence of tool calls you would make (JSON blocks, with "
    "one short sentence of intent before each), then a final user-facing answer "
    "(Arabic for Arabic tasks, English for English tasks). Be precise and "
    "complete — your output trains a smaller model to act like you."
)

TASKS = [
    {"id": "chat-greet-ar", "cat": "chat", "prompt": "مرحبا آلي! عرّف نفسك بجملتين وقل لي ماذا تستطيع أن تفعل لي.",
     "verify": {"kind": "reply_contains_any", "values": ["آلي", "أنا آلي"]}},
    {"id": "chat-greet-en", "cat": "chat", "prompt": "Hello Aali! What can you do for me today?",
     "verify": {"kind": "reply_contains_any", "values": ["Aali", "help"]}},
    {"id": "folder-structure-ar", "cat": "files",
     "prompt": "أنشئ داخل مجلد العمل مجلداً باسم arena_r{round} وبداخله ثلاثة مجلدات فرعية: src و docs و tests، واكتب داخل كل واحد ملف placeholder.txt يحتوي اسم المجلد.",
     "verify": {"kind": "paths_exist", "paths": ["arena_r{round}/src/placeholder.txt", "arena_r{round}/docs/placeholder.txt", "arena_r{round}/tests/placeholder.txt"]}},
    {"id": "config-file-en", "cat": "files",
     "prompt": "Create arena_r{round}/app_config.yaml for a web service (database, logging, mail, rate limits) with an Arabic comment above each section.",
     "verify": {"kind": "paths_exist", "paths": ["arena_r{round}/app_config.yaml"]}},
    {"id": "csv-dataset-en", "cat": "files",
     "prompt": "Create arena_r{round}/sales.csv with exactly 20 rows (date, region, product, units, revenue) of believable data, then use run_command with python to count the rows and print the total revenue.",
     "verify": {"kind": "paths_exist", "paths": ["arena_r{round}/sales.csv"]}},
    {"id": "arabic-content-ar", "cat": "files",
     "prompt": "اكتب في arena_r{round}/coffee_menu.md قائمة قهوة مختصة عربية: 6 أنواع مع الوصف والسعر، وقسم للنصائح.",
     "verify": {"kind": "path_contains", "path": "arena_r{round}/coffee_menu.md", "contains": "القهوة"}},
    {"id": "script-run-en", "cat": "code",
     "prompt": "Create arena_r{round}/analyze.py that prints the numbers 1..10 with their squares, then actually run it with run_command and show me its output.",
     "verify": {"kind": "paths_exist", "paths": ["arena_r{round}/analyze.py"]}},
    {"id": "emoji-gen-en", "cat": "media",
     "prompt": "Generate a happy emoji image for me as arena_r{round}/happy_emoji.png using your emoji generator tool.",
     "verify": {"kind": "paths_exist", "paths": ["arena_r{round}/happy_emoji.png"]}},
    {"id": "web-fetch-en", "cat": "web",
     "prompt": "Use fetch_url on https://example.com and tell me the page's main heading.",
     "verify": {"kind": "reply_contains_any", "values": ["Example Domain"]}},
    {"id": "web-fetch-ar", "cat": "web",
     "prompt": "استخدم أداة fetch_url على الرابط https://example.com وقول لي عنوان الصفحة الرئيسي.",
     "verify": {"kind": "reply_contains_any", "values": ["Example Domain"]}},
    {"id": "memory-save-ar", "cat": "memory",
     "prompt": "احفظ في ذاكرتك أن اسمي حمام وأني أحب القهوة المختصة، ثم أكّد لي أنك حفظتها.",
     "verify": {"kind": "reply_contains_any", "values": ["حفظت", "تم", "ذكرة", "ذاكرتي"]}},
    {"id": "explain-self-ar", "cat": "chat", "prompt": "اشرح لي بإيجاز: ما الفرق بينك وبين أي مساعد سحابي؟",
     "verify": {"kind": "reply_min_len", "min": 80}},
    {"id": "multi-file-en", "cat": "files",
     "prompt": "In arena_r{round}/ create notes/ideas.txt with 3 product ideas and notes/todo.txt with 5 todo lines, then list the notes folder to confirm.",
     "verify": {"kind": "paths_exist", "paths": ["arena_r{round}/notes/ideas.txt", "arena_r{round}/notes/todo.txt"]}},
    {"id": "readback-en", "cat": "code",
     "prompt": "Create arena_r{round}/quote.txt containing exactly the line: The best way out is always through. Then read the file back and quote it in your reply.",
     "verify": {"kind": "path_contains", "path": "arena_r{round}/quote.txt", "contains": "The best way out is always through"}},
    {"id": "tool-plan-ar", "cat": "files",
     "prompt": "خطة عملية: أنشئ arena_r{round}/plan.md فيها خطة بناء متجر إلكتروني صغير (5 مراحل، كل مرحلة ببنود)", 
     "verify": {"kind": "paths_exist", "paths": ["arena_r{round}/plan.md"]}},
    {"id": "error-recovery-en", "cat": "code",
     "prompt": "Create arena_r{round}/buggy.py that prints 2+2, but first write it with a syntax error on purpose, run it, observe the error, fix it, and run it again successfully. Tell me both outputs.",
     "verify": {"kind": "paths_exist", "paths": ["arena_r{round}/buggy.py"]}},
]


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)


def aali_stream(message: str) -> tuple[str, list[str], float]:
    """Ask Aali via SSE; returns (reply, tools_used, seconds)."""
    started = time.time()
    payload = json.dumps({"message": message}).encode("utf-8")
    request = urllib.request.Request(
        f"{AALI}/api/ask/stream", data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": AALI_KEY},
        method="POST",
    )
    reply, tools = "", []
    try:
        with urllib.request.urlopen(request, timeout=420) as response:
            buffer = b""
            while True:
                chunk = response.read(1)
                if not chunk:
                    break
                buffer += chunk
                if not buffer.endswith(b"\n\n"):
                    continue
                frame, buffer = buffer, b""
                event_name, data_lines = "message", []
                for line in frame.decode("utf-8", "replace").split("\n"):
                    if line.startswith("event: "):
                        event_name = line[7:].strip()
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                if not data_lines:
                    continue
                try:
                    body = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    continue
                if event_name == "activity":
                    tool = body.get("tool")
                    if tool and tool not in tools:
                        tools.append(tool)
                elif event_name == "done":
                    reply = str(body.get("reply", ""))
    except Exception as exc:  # noqa: BLE001
        reply = f"[arena error] {exc}"
    return reply, tools, time.time() - started


def claude_reference(message: str) -> tuple[str, bool, float]:
    """Ask Claude-via-OmniRoute for the reference behavior; returns (text, emitted_tool_json, seconds)."""
    started = time.time()
    payload = json.dumps({
        "model": "auto",
        "messages": [
            {"role": "system", "content": SYSTEM_CLAUDE},
            {"role": "user", "content": message},
        ],
        "temperature": 0.2, "max_tokens": 3000, "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        GATEWAY, data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body["choices"][0]["message"].get("content") or ""
    except Exception as exc:  # noqa: BLE001
        return f"[gateway error] {exc}", False, time.time() - started
    emitted = '"tool"' in text and '"arguments"' in text
    return text, emitted, time.time() - started


def verify(aali_reply: str, spec: dict, round_id: str) -> tuple[bool, str]:
    kind = spec.get("kind")
    if kind == "reply_contains_any":
        ok = any(v in aali_reply for v in spec.get("values", []))
        return ok, "reply text"
    if kind == "reply_min_len":
        return len(aali_reply) >= int(spec.get("min", 50)), f"len={len(aali_reply)}"
    if kind == "paths_exist":
        missing = [p for p in spec.get("paths", [])
                   if not (WORKSPACE / p.format(round=round_id)).is_file()]
        return (not missing), ("all files exist" if not missing else f"missing: {missing}")
    if kind == "path_contains":
        path = WORKSPACE / str(spec.get("path", "")).format(round=round_id)
        if not path.is_file():
            return False, f"{path.name} not created"
        try:
            ok = spec.get("contains", "") in path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return False, f"unreadable: {exc}"
        return ok, ("content matches" if ok else "content mismatch")
    return False, f"unknown verify kind {kind}"


def append(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_report(rows: list[dict]) -> None:
    total = len(rows)
    aali_ok = sum(1 for r in rows if r["aali_verified"])
    tools_used = sum(1 for r in rows if r["aali_tools"])
    ref_tools = sum(1 for r in rows if r["claude_tools"])
    lines = [
        "# Arena — Aali vs Claude (overnight)",
        f"_updated {datetime.now().isoformat(timespec='seconds')} — {total} task-runs_",
        "",
        "| metric | value |",
        "|---|---|",
        f"| Aali artifacts verified | **{aali_ok}/{total}** |",
        f"| Aali used tools | {tools_used}/{total} |",
        f"| Claude reference used tools | {ref_tools}/{total} |",
        f"| Aali avg reply time | {sum(r['aali_secs'] for r in rows) / max(total, 1):.0f}s |",
        "",
        "| task | Aali verified | Aali tools | reply secs | Claude tools |",
        "|---|---|---|---|---|",
    ]
    for r in rows[-60:]:
        lines.append(
            f"| {r['task']} | {'✅' if r['aali_verified'] else '❌'} "
            f"| {', '.join(r['aali_tools']) or '—'} | {r['aali_secs']:.0f} "
            f"| {'✓' if r['claude_tools'] else '—'} |"
        )
    lines += [
        "",
        "## Top gaps (Aali fell short of the reference)",
    ]
    gaps = [r for r in rows if not r["aali_verified"]]
    if not gaps:
        lines.append("- none yet 🎉")
    for r in gaps[-15:]:
        lines.append(f"- **{r['task']}**: {r['note']}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ARENA.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    log("arena start — Aali (CPU) vs Claude reference (OmniRoute)")
    all_rows: list[dict] = []
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            try:
                all_rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        log(f"resuming with {len(all_rows)} previous records")
    round_no = len({r.get("round") for r in all_rows}) + 1
    while round_no <= MAX_ROUNDS:
        if 7 <= datetime.now().hour < 19:  # night shift only: 19:00 → 07:00
            log("daytime — stopping before a new round")
            break
        log(f"=== round {round_no} ===")
        for task in TASKS:
            task_id = task["id"]
            round_id = str(round_no)
            prompt = task["prompt"].replace("{round}", round_id)
            log(f"  AALI  : {task_id}")
            reply, tools, secs = aali_stream(prompt)
            ok, note = verify(reply, task.get("verify", {}), round_id)
            log(f"        -> {'VERIFIED' if ok else 'FAILED'} ({secs:.0f}s, tools: {tools or '—'})")
            log(f"  CLAUDE: {task_id}")
            reference, ref_tools, ref_secs = claude_reference(prompt)
            log(f"        -> {'tool plan' if ref_tools else 'no tools'} ({ref_secs:.0f}s)")
            record = {
                "round": round_no, "task": task_id, "cat": task["cat"],
                "aali_verified": ok, "note": note, "aali_tools": tools,
                "aali_secs": round(secs, 1), "claude_tools": ref_tools,
                "claude_secs": round(ref_secs, 1),
                "aali_reply_head": reply[:400],
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
            append(RESULTS, record)
            all_rows.append(record)
            if not ok and ref_tools:
                gap = {
                    "source": "arena_gap", "task": task_id, "round": round_no,
                    "messages": [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": reference},
                    ],
                    "why": f"Aali failed verification: {note}",
                }
                append(GAPS, gap)
            write_report(all_rows)
            time.sleep(SETTLE_SECONDS)
        round_no += 1
    write_report(all_rows)
    log(f"arena done — {len(all_rows)} records. Report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
