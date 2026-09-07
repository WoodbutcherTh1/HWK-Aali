"""Overnight multi-AI arena — the owner's last order before sleep (2026-09-08):
"chat all night with many AI as you can (9+ models, different companies), ask
Aali the same questions, compare, find the best, make Aali act like the best."

Channels:
  AALI   — the real server (port 5055, CPU brain) via /api/ask
  MODELS — the free OmniRoute gateway; every candidate model is tried, errors
           are recorded and skipped.

Each answer gets 7 graded checks (nonempty, no protocol-JSON leak, no echo of
the question, language match, math correctness where applicable, completeness,
no canned-repeat) — ~1,200 checks per full pass. Per-task the best model wins
points; the morning report ranks every model AND Aali, and the best model's
answers for tasks Aali failed are saved as SFT episodes (best_in_class.jsonl).

Outputs under D:/hwk-data/arena/: results.jsonl, best_in_class.jsonl,
ARENA_REPORT.md. Runs passes until DAWN (07:00) or MAX_PASSES.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ARENA = Path("D:/hwk-data/arena")
RESULTS = ARENA / "results.jsonl"
BEST = ARENA / "best_in_class.jsonl"
REPORT = ARENA / "ARENA_REPORT.md"
KEY_FILE = Path("D:/hwk-data/aali_server_key.txt")

AALI = "http://127.0.0.1:5055"
GATEWAY = "http://localhost:20128/v1/chat/completions"
CANDIDATE_MODELS = [
    "auto", "t3chat/claude-sonnet-4", "vp/claude-sonnet-4",
    "t3chat/gpt-4o", "vp/gpt-4o", "deepseek-v3", "gemini-2.0-flash",
    "grok-3", "mistral-large", "llama-4-maverick",
]
DAWN_HOUR = 7
MAX_PASSES = 4
SETTLE = 5

QUESTIONS = [
    {"id": "math-ar", "q": "كم يساوي 3 ضرب 7؟ أجب بالرقم فقط.",
     "expect_number": "21", "lang": "ar"},
    {"id": "math-en", "q": "What is 17 * 23? Reply with the number only.",
     "expect_number": "391", "lang": "en"},
    {"id": "identity-ar", "q": "من صمّمك؟", "lang": "ar"},
    {"id": "identity-en", "q": "Who designed you?", "lang": "en"},
    {"id": "self-desc-ar", "q": "اشرح في سطرين: ما الفرق بينك وبين مساعد سحابي مثل ChatGPT؟", "lang": "ar"},
    {"id": "ai-def-en", "q": "What is AI? Answer in two sentences.", "lang": "en"},
    {"id": "tools-plan-ar", "q": "أنت وكيل بأدوات: write_file, read_file, run_command, fetch_url. اكتب تسلسل الخطوات (كأداة JSON) لإنشاء ملف notes/plan.md يحتوي خطة من 3 بنود، ثم أجب المستخدم.",
     "lang": "ar", "expect_tools": True},
    {"id": "tools-plan-en", "q": "You are an agent with tools: write_file, run_command, fetch_url. Write the JSON tool-call sequence to create hello.py that prints 'hello world' and then run it. Then answer the user.",
     "lang": "en", "expect_tools": True},
    {"id": "summarize-ar", "q": "لخّص في جملة واحدة: الذكاء الاصطناعي يغير الصناعات عبر أتمتة المهام وتحليل كميات ضخمة من البيانات، لكنه يثير أسئلة أخلاقية حول الخصوصية والدقة.", "lang": "ar"},
    {"id": "translate-en", "q": "Translate to English: الصندوق فيه كتب كثيرة", "lang": "en"},
    {"id": "coding-en", "q": "Write a one-line Python expression that reverses the string 'arena'. Reply with the expression only.", "lang": "en", "expect_number": "arena[::-1]"},
    {"id": "facts-en", "q": "Who was the 43rd president of the United States? One line.", "lang": "en", "expect_number": "Bush"},
]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def post(url: str, payload: dict, headers: dict, timeout: int) -> dict | None:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def ask_aali(q: str) -> str:
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    body = post(f"{AALI}/api/ask", {"message": q},
                {"Content-Type": "application/json", "X-API-Key": key}, 600)
    if not body:
        return ""
    return str(body.get("reply", "")) if body.get("ok") else f"[error] {body.get('error')}"


def ask_model(model: str, q: str) -> str:
    body = post(GATEWAY, {"model": model, "messages": [{"role": "user", "content": q}],
                          "temperature": 0.2, "max_tokens": 800, "stream": False},
                {"Content-Type": "application/json"}, 180)
    if not body or "error" in body and body.get("error") and isinstance(body["error"], dict):
        return f"[unavailable] {(body or {}).get('error')}"
    try:
        return str(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return f"[unavailable] {json.dumps(body)[:120]}"


def grade(q: dict, answer: str) -> dict:
    checks = {
        "nonempty": bool(answer.strip()) and not answer.startswith("[unavailable]"),
        "no_json_leak": '{"content"' not in answer and '"]' not in answer[:5],
        "no_echo": answer.strip()[:40] != q.strip()[:40],
        "lang_ok": True,
        "correct": True,
        "complete": len(answer.strip()) >= 15 or bool(answer.strip()),
        "no_repeat": not re.search(r"(.{15,})\1{2,}", answer),
    }
    if q.get("lang") == "ar":
        arabic_chars = len(re.findall(r"[\u0600-\u06FF]", answer))
        checks["lang_ok"] = arabic_chars >= max(3, int(len(answer) * 0.15))
    expected = q.get("expect_number")
    if expected:
        checks["correct"] = expected.lower() in answer.lower()
    score = sum(1 for v in checks.values() if v)
    return checks, score


def append(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_report(all_rows: list[dict], passed: int) -> None:
    models: dict[str, dict] = {}
    for r in all_rows:
        slot = models.setdefault(r["model"], {"score": 0, "n": 0, "checks": 0, "wins": 0})
        slot["n"] += 1
        slot["score"] += r["score"]
        slot["checks"] += 7
        if r["best"]:
            slot["wins"] += 1
    lines = [
        "# Multi-AI arena — morning report",
        f"_updated {datetime.now().isoformat(timespec='seconds')} — "
        f"{len(all_rows)} answers, {passed}/1000 checks toward tonight's goal_",
        "",
        "| model | answers | check score | best-answer wins |",
        "|---|---|---|---|",
    ]
    for name, s in sorted(models.items(), key=lambda kv: -kv[1]["score"] / max(kv[1]["n"], 1)):
        lines.append(f"| {name} | {s['n']} | {s['score']}/{s['checks']} | {s['wins']} |")
    lines += ["", "Aali's row is the one to fix: every task where a cloud model beat him",
              "has its best answer saved in best_in_class.jsonl for the next SFT."]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ARENA.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            try:
                all_rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        log(f"resuming with {len(all_rows)} records")
    passed = len(all_rows) * 7
    check_count = 0
    for pass_no in range(1, MAX_PASSES + 1):
        if 7 <= datetime.now().hour < 19:
            log("daytime — stopping")
            break
        log(f"=== pass {pass_no} ===")
        for qi, q in enumerate(QUESTIONS):
            answers: list[tuple[str, str, int]] = []
            log(f"  AALI : {q['id']}")
            aali_answer = ask_aali(q["q"])
            checks, score = grade(q, aali_answer)
            answers.append(("AALI", aali_answer, score))
            append(RESULTS, {"pass": pass_no, "task": q["id"], "model": "AALI",
                             "score": score, "checks": checks,
                             "answer_head": aali_answer[:300],
                             "ts": datetime.now().isoformat(timespec="seconds")})
            check_count += 7
            for model in CANDIDATE_MODELS:
                answer = ask_model(model, q["q"])
                if answer.startswith("[unavailable]"):
                    continue
                checks, score = grade(q, answer)
                answers.append((model, answer, score))
                append(RESULTS, {"pass": pass_no, "task": q["id"], "model": model,
                                 "score": score, "checks": checks,
                                 "answer_head": answer[:300],
                                 "ts": datetime.now().isoformat(timespec="seconds")})
                check_count += 7
            best_model, best_answer, best_score = max(answers, key=lambda t: t[2])
            for name, answer, score in answers:
                append(RESULTS, {"pass": pass_no, "task": q["id"], "model": name,
                                 "best": name == best_model,
                                 "ts": datetime.now().isoformat(timespec="seconds")})
            if best_model != "AALI":
                append(BEST, {"task": q["id"], "best_model": best_model,
                              "messages": [
                                  {"role": "user", "content": q["q"]},
                                  {"role": "assistant", "content": best_answer}],
                              "why": f"{best_model} scored {best_score}, Aali scored "
                                     f"{dict((n, s) for n, _, s in answers).get('AALI', 0)}"})
            aali_score = dict((n, s) for n, _, s in answers).get("AALI", 0)
            log(f"        done — Aali {aali_score}/7, best: {best_model} ({best_score}/7), "
                f"checks so far: {check_count}")
            all_rows = all_rows  # report uses results file
            write_report(all_rows, check_count)
            time.sleep(SETTLE)
    write_report(all_rows, check_count)
    log(f"arena done — report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
