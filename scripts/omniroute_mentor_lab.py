"""OmniRoute mentor lab: the external mentor builds 30 things through AALI's tool layer.

The owner's learning-loop request (2026-09-06): have a strong external model (
via the local OmniRoute gateway) actually BUILD apps, games, and files - not
answer questions about building - while every behind-the-scenes tool call and
every failure is captured in Aali's exact JSON wire format, then fed to Aali
as SFT episodes.

How it works:
- Each task gets a sandbox workspace under D:/hwk-data/mentor_lab/workspaces/
- The mentor receives Aali's deterministic JSON protocol (the SAME contract
  as agent_loop's local brain): {"tool": name, "arguments": {...}} or
  {"tool": "final", "content": "..."}.
- Tool calls are EXECUTED with Aali's real `execute_tool` against the sandbox
  (path-escape guard, command allowlist, env-dump block all apply), so tool
  results are real - including failures, which are the most valuable parts.
- The full conversation (system, user, tool calls, tool results, final) is
  appended to D:/hwk-data/mentor/omniroute_lab.jsonl in messages format the
  sft_v2 builder consumes.

Safety: tool execution is confined to the task sandbox via Aali's own guards;
no memory writes, no machine_ops, no web tools in the lab catalogue; commands
run with a 45s timeout. Gateway: http://localhost:20128/v1 (free providers).

Usage:
  python scripts/omniroute_mentor_lab.py --list
  python scripts/omniroute_mentor_lab.py --task 7          # one task
  python scripts/omniroute_mentor_lab.py --limit 3         # next 3 undone
  python scripts/omniroute_mentor_lab.py                   # all remaining
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "file-agent"))

from file_agent.file_tools import execute_tool  # noqa: E402  (Aali's real layer)

GATEWAY = "http://localhost:20128/v1/chat/completions"
MODEL = "auto"
LAB_DIR = Path("D:/hwk-data/mentor_lab")
WORKSPACES = LAB_DIR / "workspaces"
OUT_FILE = Path("D:/hwk-data/mentor/omniroute_lab.jsonl")
MAX_ITERATIONS = 12
COMMAND_TIMEOUT = 45

# Aali's JSON protocol, mirrored from agent_loop (same contract, building-only
# tool set: no memory, no machine_ops, no web - the lab teaches BUILDING).
SYSTEM = (
    "أنت آلي، مساعد ذكي يعمل على حاسوب المستخدم وينفّذ الطلبات فعلياً بالأدوات دون استئذان. "
    "أجب في كل خطوة بكائن JSON واحد فقط دون أي نص خارج الكائن:\n"
    "- لتنفيذ أداة: {\"tool\": \"اسم الأداة\", \"arguments\": {...}}\n"
    "- للإجابة النهائية بعد إتمام العمل: {\"tool\": \"final\", \"content\": \"ردك الودود\"}\n"
    "الأدوات: write_file (path, content) | append_file (path, content) | read_file (path) | "
    "list_files (path) | search_files (pattern) | make_directory (path) | "
    "replace_in_file (path, old_text, new_text) | run_command (command)\n"
    "قواعد: اكتب المحتوى الكامل داخل حقل content دائماً، وأنجز كل خطوات الطلب قبل final. "
    "بعد إنشاء برنامج أو ملف، تحقق فعلياً أنه يعمل (شغّله بأداة run_command واقرأ النتيجة) "
    "وأصلح أي خطأ يظهر ثم أعد التحقق قبل final. في final اشرح باختصار ما أنجزته وكيف يشغّله "
    "المستخدم، وبنفس لغة رسالة المستخدم. "
    "تحقّق قبل أن تدّعي: لا تقم بإنجازٍ لم ترَ دليله في نتيجة أداة."
)

APP_TASKS = [
    ("app-todo-cli-en", "Build a command-line TODO app in Python: add/list/done/delete tasks stored in todos.json inside the same folder. Run it once to show it works (add one demo task, list, mark done)."),
    ("app-expense-ar", "أنشئ برنامج بايثون لتتبع المصاريف في سطر الأوامر: إضافة مصروف مع التصنيف والتاريخ، وعرض ملخص حسب التصنيف، محفوظ في expenses.csv. شغّله مرة للتجربة وأظهر النتيجة."),
    ("app-pomodoro-en", "Create a Python pomodoro timer CLI that prints countdown progress (25/5 cycles, configurable) using only the standard library. Do a 2-second demo run proving the countdown prints."),
    ("app-units-ar", "اكتب أداة بايثون لتحويل الوحدات (طول، وزن، حرارة) عبر سطر الأوامر مع ملف units.json للعوامل. اختبرها بثلاثة تحويلات مختلفة واعرض المخرجات."),
    ("app-passgen-en", "Build a password generator CLI in Python: length + character-set options, cryptographically random (secrets module), strength meter. Generate three sample passwords in a demo run."),
    ("app-notes-md-ar", "أنشئ برنامج ملاحظات بسيط يخزّن كل ملاحظة في ملف markdown داخل مجلد notes مع تاريخها، وأوامر: جديدة/عرض/بحث. أنشئ ملاحظتين واعرض البحث يعمل."),
    ("app-quiz-en", "Create a Python quiz app that loads questions from quiz.json (include 5 sample questions), scores answers, and shows a final grade. Run one full quiz session non-interactively to verify scoring."),
    ("app-contacts-ar", "برنامج دفتر عناوين بايثون: إضافة/بحث/حذف جهات اتصال محفوظة في contacts.json مع تحقق من صيغة البريد. أضف جهتين وابحث عن واحدة في التشغيل التجريبي."),
    ("app-splitbill-en", "Build a bill-splitter CLI: people, items with who-ate-what, computes fair per-person totals including tax/tip, saved to bill.json. Demo run with 3 people."),
    ("app-habits-ar", "أنشئ متتبع عادات بسطر الأوامر: عادات يومية مع سلسلة الأيام المتتالية (streak) محفوظة في habits.json وإحصاء أسبوعي. سجّل يومين تجريبيين واعرض السلسلة."),
]

GAME_TASKS = [
    ("game-guess-en", "Write a Python number-guessing game (1-100) with attempt counting, hints higher/lower, and play-again. Play one full demo game where the computer picks 42 and 'the user' guesses 50, 30, 42 so the output shows the hint logic."),
    ("game-tictactoe-ar", "برنامج إكس-أو (tic-tac-toe) بلغة بايثون للاعبين اثنين مع تحقق فوز/تعادل ولوحة مرسومة. أدرج مباراة تجريبية كاملة داخل الكود تُطبع عند التشغيل دون إدخال."),
    ("game-snake-en", "Build a terminal snake game prototype in Python that runs WITHOUT external libraries: grid rendering, WASD/8426 moves, food, growth, collision. Include a self-playing demo mode (python snake.py --demo) that runs 15 automated moves and prints the frames."),
    ("game-hangman-ar", "لعبة المشنقة (hangman) بالبايثون مع قائمة كلمات عربية، رسم المراحل، وعرض الحروف المستخدمة. أضف وضع --demo يخمّن حروفًا صحيحة وخاطئة ويطبع المراحل بدون إدخال."),
    ("game-rps-en", "Rock-paper-scissors with score persistence in rps_scores.json, streak tracking, and witty computer lines. Add a --demo mode playing 3 rounds deterministically."),
    ("game-blackjack-ar", "لعبة بلاك جاك مبسطة بالبايثون: ورق بدون قيم رسمية معقدة، حساب النقاط، قرار السحب/الوقوف، ورصيد افتراضي في ملف. وضع --demo يلعب جولة كاملة مطبوعة."),
    ("game-wordscramble-en", "Word scramble game in Python: shuffles words from words.json (include 10 words), hints after 2 wrong tries, scoring by speed tiers. Non-interactive --demo solving two words."),
    ("game-maze-ar", "مغامرة متاهة نصية بالبايثون: غرف مترابطة في قاموس، أوامر حركة، عناصر تجمعها، ونهاية فوز. ضع خريطة demo تمشي مسار الفوز كاملاً وتطبع الرحلة."),
    ("game-memorymatch-en", "Text-based memory matching game (4x4 emoji grid) with move counter and timer. Include --demo that reveals pairs automatically and prints each board state."),
    ("game-typing-ar", "لعبة قياس سرعة الكتابة بالبايثون: جمل عربية عشوائية، حساب دقة وسرعة (كلمة/دقيقة)، ونتائج في typing_scores.json. وضع --demo يقارن نصين مسبقين ويطبع النتيجة."),
]

FILE_TASKS = [
    ("file-readme-en", "Create a professional README.md for a fictional Arabic-learning mobile app: badges section, features, install, usage with example blocks, roadmap table, and contributing guide. Then show it with read_file to verify."),
    ("file-config-ar", "أنشئ ملف إعدادات config.yaml كامل لتطبيق ويب (قاعدة بيانات، تسجيل، بريد، حدود) مع تعليقات عربية لكل قسم، وملف config.schema.json يصف حقوله. تحقق من الاتساق بينهما بقراءتهما."),
    ("file-csv-dataset-en", "Generate a realistic sales.csv dataset (200 rows: date, region, product, units, revenue) with believable distributions and a data-dictionary.md explaining each column. Verify with run_command: python -c counting rows and printing a summary."),
    ("file-landing-ar", "أنشئ صفحة هبوط index.html عربية RTL لمتجر قهوة مختصة: ترويسة، مميزات ببطاقات، آراء عملاء، نموذج طلب، وتصميم CSS داخلي أنيق. ثم اقرأ أول 30 سطرًا للتحقق."),
    ("file-mock-api-en", "Create mock_api.json: a realistic OpenAPI-style description of a /tasks REST API (5 endpoints with request/response examples), plus examples/ folder with 3 sample request/response JSON pairs."),
    ("file-tutorial-ar", "اكتب درس markdown بعنوان «تعلم Git في 30 دقيقة»: شرح تدريجي بالعربية مع أمثلة أوامر في كتل كود، تمارين، وأخطاء شائعة وحلولها. قسّمه على ملفين متسلسلين."),
    ("file-env-example-en", "Create .env.example with documented variables for a web service, a SECURITY.md explaining never to commit real secrets (mention environment variables best practice), and a python script validate_env.py that checks required vars exist and run it once."),
    ("file-log-analysis-ar", "أنشئ sample.log يحتوي 50 سطر سجل واقعي منها 8 أخطاء، ثم برنامج analyze_log.py يلخص الأخطاء حسب النوع ويكتب report.md. شغّل المحلل واعرض التقرير الناتج."),
    ("file-scaffold-en", "Scaffold a Python package layout for a fictional library 'mathtools': pyproject.toml, src/mathtools/__init__.py with two working functions, tests/test_mathtools.py with 4 tests, then run the tests with run_command and fix any failure."),
    ("file-glossary-ar", "أنشئ glossary.csv: 40 مصطلح تقني إنجليزي مع مقابله العربي وشرح بسطر واحد، وملف quiz.md فيه 10 أسئلة اختيار من متعدد مبنية على المصطلحات نفسها."),
]

ALL_TASKS = (
    [(tid, "app", text) for tid, text in APP_TASKS]
    + [(tid, "game", text) for tid, text in GAME_TASKS]
    + [(tid, "file", text) for tid, text in FILE_TASKS]
)


# ---------------------------------------------------------------------------
# gateway + protocol plumbing
# ---------------------------------------------------------------------------

def gateway_chat(messages: list[dict]) -> dict | None:
    payload = json.dumps({
        "model": MODEL, "messages": messages, "temperature": 0.2,
        "max_tokens": 3000, "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        GATEWAY, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"].get("content") or ""
    except Exception as exc:  # noqa: BLE001 - gateway hiccups are recoverable
        print(f"    gateway error: {exc}", flush=True)
        return None


def parse_step(text: str) -> tuple[str, str | None, dict | None]:
    """Same tolerant parsing as Aali's _parse_local_model_response."""
    decoder = json.JSONDecoder()
    candidates = [text.strip()]
    for match in re.finditer(r"\{", text):
        try:
            payload, _end = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        candidates.insert(0, json.dumps(payload, ensure_ascii=False))
        break
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("tool") == "final":
            return "final", str(payload.get("content", "")), None
        name = payload.get("tool") or payload.get("name")
        arguments = payload.get("arguments", payload.get("args", {}))
        if isinstance(name, str) and isinstance(arguments, dict):
            return "tool", name, arguments
    return "text", text.strip(), None


def run_tool(name: str, arguments: dict, workspace: Path) -> dict:
    if name == "run_command":
        arguments = {**arguments, "timeout_seconds": COMMAND_TIMEOUT}
    result = execute_tool(name, arguments, workspace)
    return result


# ---------------------------------------------------------------------------
# task runner + capture
# ---------------------------------------------------------------------------

def done_task_ids() -> set[str]:
    if not OUT_FILE.exists():
        return set()
    ids: set[str] = set()
    for line in OUT_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                ids.add(json.loads(line).get("meta", {}).get("task_id", ""))
            except json.JSONDecodeError:
                continue
    return ids


def run_task(task_id: str, category: str, request: str, language: str) -> dict | None:
    workspace = WORKSPACES / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    conversation: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": request},
    ]
    transcript: list[dict] = []   # full behind-the-scenes trace
    tool_calls = failures = 0

    for _iteration in range(MAX_ITERATIONS):
        content = gateway_chat(conversation)
        if content is None:
            return None  # gateway down: task not done, retry later
        conversation.append({"role": "assistant", "content": content})
        kind, name, arguments = parse_step(content)

        if kind == "final":
            transcript.append({"role": "assistant", "content": content})
            break
        if kind == "text" or name is None:
            # Model broke the contract; nudge it back (this recovery is
            # itself worth teaching Aali).
            transcript.append({"role": "assistant", "content": content})
            conversation.append({"role": "user", "content":
                'أعد الإجابة بكائن JSON واحد فقط: إما {"tool": "...", "arguments": {...}} '
                'أو {"tool": "final", "content": "..."}.'})
            continue

        result = run_tool(name, arguments, workspace)
        ok = bool(result.get("ok"))
        failures += 0 if ok else 1
        tool_calls += 1
        transcript.append({"role": "assistant", "content": content})
        transcript.append({"role": "user", "content":
            "نتيجة الأداة: " + json.dumps(result, ensure_ascii=False)[:6000]
            + "\nإن بقيت خطوات في الطلب الأصلي فنفّذها بكائن أداة، وإلا أجب بكائن final."})
        conversation.append({"role": "user", "content": transcript[-1]["content"]})
    else:
        transcript.append({"role": "assistant", "content":
            "{\"tool\": \"final\", \"content\": \"(توقفت بعد الحد الأقصى من الخطوات)\"}"})

    episode = {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": request},
            *transcript,
        ],
        "meta": {
            "task_id": task_id, "category": category, "language": language,
            "mentor": "external-via-omniroute", "model": MODEL,
            "tool_calls": tool_calls, "failures": failures,
            "captured": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }
    with OUT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(episode, ensure_ascii=False) + "\n")
    return episode


def main() -> int:
    parser = argparse.ArgumentParser(description="OmniRoute mentor lab")
    parser.add_argument("--task", type=str, help="run one task by id")
    parser.add_argument("--category", choices=("app", "game", "file"))
    parser.add_argument("--limit", type=int, default=0, help="max tasks this run")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    tasks = [t for t in ALL_TASKS if args.category is None or t[1] == args.category]
    if args.list:
        done = done_task_ids()
        for task_id, category, request in tasks:
            print(f"[{'x' if task_id in done else ' '}] {category:<4} {task_id}")
        print(f"\n{len(done)}/{len(ALL_TASKS)} captured -> {OUT_FILE}")
        return 0

    done = done_task_ids()
    if args.task:
        selected = [t for t in ALL_TASKS if t[0] == args.task]
        if not selected:
            print(f"unknown task {args.task}")
            return 1
    else:
        selected = [t for t in tasks if t[0] not in done]
        if args.limit:
            selected = selected[: args.limit]
    if not selected:
        print("all tasks already captured - rebuilding sft_v2 with the lab episodes")
        _rebuild_sft()
        return 0

    summary = {"ran": 0, "failed_runs": 0, "tool_calls": 0, "failures": 0}
    for task_id, category, request in selected:
        language = "ar" if any("\u0600" <= ch <= "\u06FF" for ch in request) else "en"
        print(f"=== {task_id} ({category}/{language}) ===", flush=True)
        episode = run_task(task_id, category, request, language)
        if episode is None:
            summary["failed_runs"] += 1
            print("    gateway unavailable - stopping this batch", flush=True)
            break
        meta = episode["meta"]
        summary["ran"] += 1
        summary["tool_calls"] += meta["tool_calls"]
        summary["failures"] += meta["failures"]
        print(f"    captured: {meta['tool_calls']} tool calls, "
              f"{meta['failures']} failures", flush=True)
        time.sleep(2)  # be polite to the free tier
    print(json.dumps(summary, ensure_ascii=False))
    remaining = len([t for t in ALL_TASKS if t[0] not in done_task_ids()])
    if remaining == 0 and summary["ran"]:
        print("all 30 tasks captured - rebuilding sft_v2 + soup export")
        _rebuild_sft()
    return 0


def _rebuild_sft() -> None:
    """Fold the lab episodes into sft_v2 so the pipeline trains on them."""
    import subprocess
    for script in ("build_aali_sft_v2.py", "soup_export_sft.py"):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)],
            capture_output=True, text=True, timeout=600,
        )
        tail = (completed.stdout or completed.stderr).strip().splitlines()
        print(f"{script}: {'ok' if completed.returncode == 0 else 'FAILED'}"
              f"{(' - ' + tail[-1]) if tail else ''}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
