"""Distill the PROMOTED teacher into scratch-shaped SFT rows (sft_distill.jsonl).

Why (tasks/distill-teacher-scratch.md): run-8 proved the SCRATCH row shape
is right but the mix's completion signal is weak — sft_v2 rows were authored
for a 1.5B Qwen ADAPTER. This builder asks the teacher that already passed
the exam (checkpoint-3873 serving on :20129/v1) to answer SEED prompts
RENDERED IN THE SCRATCH SERVING SHAPE (soup_exam.ask pattern: the rendered
prompt travels as a single user message — the teacher saw this exact shape
when it was graded), then writes the answers as completions the Phase C
trainer learns from.

Quality gates (every drop is named in the report; nothing silent):
  - self-leak   : any 8-word shingle of the seed prompt in the reply
                  (the leak filter's L1 rule, mirrored here)
  - canary      : XMARK-7731 / LEXSEAL-5219 / GLINTQUILL-3407 in a reply
  - exam-leak   : a seed colliding with an exam user prompt FAILS the build
                  loudly at startup (v3 media-lesson: collision was silent)
  - tool schema : tool JSON must carry registry-or-alias names (parsed and
                  re-dumped, never string-replaced) + required schema args
  - kind-aware  : tool-call answers are only valid for tool/memory/honesty
                  seeds — a chat/capability seed answered with a tool call is
                  dropped (tool-for-chat), and a refusal seed answered with a
                  tool call is COMPLIANCE (dropped loudly)
  - refusal cue : a refusal seed's plain-text answer must carry an actual
                  refusal cue (AR/EN list) — compliant prose never trains
  - fenced JSON : markdown-fenced replies are unwrapped before judging
                  (the teacher fences tool JSON; unwrapped fenced junk falls
                  through to the same gates as raw JSON)
  - final-JSON  : a teacher final-envelope reply is unwrapped to plain text
                  (the scratch path serves plain answers; run-7 lesson)
  - language    : AR seeds must get Arabic-script replies, EN seeds
                  Latin-script replies (the run-6 salad mixed languages)
  - budget      : v2 token estimator — answer <= ANSWER_TOKEN_BUDGET,
                  total <= TRAIN_MAX_LENGTH (the scratch row constants)
  - empty/format: empty replies and replies that ARE the seed echoed back

Identity seeds NEVER reach the teacher: the deterministic _identity_fast
card is the answer (the owner's identity-truth rule — a 7B soup brain once
answered "trained by Alibaba Cloud"; the card is the only permitted answer).

Output: D:/hwk-data/distill/sft_distill.jsonl (+ .report.json, counters
only). The Phase C trainer consumes it via AALI_PHASE_C_DATA — launching
run 9 stays a human decision.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "file-agent"))
sys.path.insert(0, str(ROOT))  # train_scratch.py lives at the repo root
                               # (bare/detached runs get no pytest conftest)

import build_aali_sft_v2 as v2  # noqa: E402  (estimator only)
from build_aali_sft_scratch import (  # noqa: E402
    ANSWER_TOKEN_BUDGET,
    TRAIN_MAX_LENGTH,
    _registry_names,
    _repair_tool_names,
    _unwrap_final,
)

SEEDS = ROOT / "data" / "distill_seeds.jsonl"
EXAM = ROOT / "data" / "exam_tool_calling.jsonl"
OUTPUT = Path("D:/hwk-data/distill/sft_distill.jsonl")
REPORT = OUTPUT.with_suffix(".report.json")
INCIDENTS = Path("D:/hwk-data/security_incidents.log")

TEACHER_BASE_URL = "http://127.0.0.1:20129/v1"
TEACHER_MODEL = "checkpoint-3873"
REQUEST_TIMEOUT = 180
REQUEST_GAP_S = 0.4
MAX_RETRIES = 2

_CANARIES = ("XMARK-7731", "LEXSEAL-5219", "GLINTQUILL-3407")
_CANARY_RE = re.compile("|".join(re.escape(c) for c in _CANARIES))
_TOOL_CALL_RE = re.compile(
    r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*')
_FENCE_RE = re.compile(
    r"^```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)(?:\r?\n)?```[ \t]*\r?\n?$",
    re.DOTALL)
# kinds where a tool-call answer is a legitimate completion
_TOOL_CALL_KINDS = ("tool", "memory", "honesty")
# a refusal seed's answer must actually refuse (AR + EN cues, lowercase)
_REFUSAL_CUES = (
    "لا أقدر", "لا يمكنني", "لا يمكن", "لا استطيع", "لا أستطيع", "ممنوع",
    "محجوب", "لن أفعل", "أرفض", "أعتذر", "عذراً", "عفواً", "ما أقدر",
    "لأسباب أمنية", "لأسباب أمان",
    "can't", "cannot", "can not", "won't", "will not", "not able",
    "unable", "refuse", "not allowed", "must not", "won\u2019t",
    "can\u2019t", "i'm not comfortable", "not comfortable",
)
_AR_RE = re.compile(r"[\u0600-\u06FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _tool_required_args() -> dict[str, tuple[str, ...]]:
    """name -> required args from the LIVE definitions' JSON schemas
    (single source of truth — TOOL_EXECUTION maps execution targets, not
    schemas; read fresh per call, the tool_guard no-cache lesson)."""
    try:
        from file_agent.file_tools import get_tool_definitions
        table: dict[str, tuple[str, ...]] = {}
        for d in get_tool_definitions():
            fn = d.get("function", {}) if isinstance(d, dict) else {}
            name = fn.get("name")
            if isinstance(name, str):
                required = fn.get("parameters", {}).get("required", ())
                table[name] = tuple(required) if isinstance(required, list) else ()
        return table
    except Exception:  # noqa: BLE001 - schema source is optional
        return {}


def _shingles(text: str, n: int = 8) -> set[str]:
    """8-word shingles — the exact _shingles shape from agent_loop's L1."""
    words = re.findall(r"\w+", (text or "").lower())
    if len(words) < n:
        return set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _hash_pair(user_text: str, assistant_text: str) -> str:
    return hashlib.sha1(
        (user_text + "\x00" + assistant_text).encode("utf-8")).hexdigest()


def load_exam_fingerprints(path: Path) -> set[str]:
    """v2.load_exam_prompts logic + raw-shingle guard.

    load_exam_prompts hashes ONLY 8+ word prompts (shorter ones produce no
    shingles), and my seeds share exact wording with several exam prompts —
    the v3 media-lesson leak. The raw-prompt set catches every length.
    """
    from build_aali_sft_v2 import load_exam_prompts
    fingerprints = load_exam_prompts(path)
    if not path.exists():
        return fingerprints
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError:
            continue
        for _role, text in case.get("turns", []):
            if isinstance(text, str):
                fingerprints.add(_hash_pair(text.strip(), ""))
        prompt = str(case.get("prompt", ""))
        if prompt:
            match = re.search(r"\nUser:\s*(.*?)\nAssistant:\s*$", prompt,
                              re.DOTALL)
            if match:
                fingerprints.add(_hash_pair(match.group(1).strip(), ""))
    return fingerprints


def load_seeds(path: Path) -> list[dict]:
    seeds: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        seed = json.loads(line)
        for key in ("id", "kind", "lang", "seed"):
            if not seed.get(key):
                raise SystemExit(f"seed missing {key}: {seed}")
        if seed["lang"] not in ("ar", "en"):
            raise SystemExit(f"seed lang must be ar|en: {seed['id']}")
        seeds.append(seed)
    if len({s["id"] for s in seeds}) != len(seeds):
        raise SystemExit("duplicate seed ids")
    return seeds


def _render_scratch_prompt(seed_text: str) -> str:
    """The exact serving-shape prompt the teacher is asked with.

    train_scratch._sft_prompt_text(record) for a single-user record renders
    System line + tools + 'User: <seed>' + SCRATCH_INSTRUCTION; passing it
    as ONE user message is how soup_exam asked the teacher when it was
    graded — shapes match (run-7 lesson). The live tool list is read fresh
    (tool_guard lesson: never cache).
    """
    from train_scratch import SCRATCH_INSTRUCTION, SCRATCH_SYSTEM_LINE
    from file_agent.file_tools import get_tool_definitions
    names = [d.get("function", {}).get("name") for d in get_tool_definitions()
             if isinstance(d, dict)]
    tools_line = f"Available tools: {', '.join(n for n in names if n)}\n"
    return (SCRATCH_SYSTEM_LINE + tools_line
            + f"User: {seed_text}\n" + SCRATCH_INSTRUCTION)


def _identity_card(seed_text: str) -> str | None:
    """The deterministic card when _identity_fast matches; None = not identity.

    'مين أنت بالضبط؟' passes a human eye but not _identity_fast's regexes —
    an identity-kind seed that the fast-path does NOT verify must be dropped
    loudly in build() rather than sent to the teacher (whose maker story is
    exactly what must never enter training rows).
    """
    from agent_loop import _identity_fast
    return _identity_fast(seed_text)


def ask_teacher(prompt: str, *, base_url: str = TEACHER_BASE_URL,
                model: str = TEACHER_MODEL, timeout: int = REQUEST_TIMEOUT,
                transport=None) -> str:
    """One teacher call: rendered prompt as a single user message.
    transport is injectable for tests; default = soup_exam-style HTTP."""
    if transport is not None:
        return transport(prompt)
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0.0,
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _arabic_ratio(reply: str) -> float:
    if not reply:
        return 0.0
    return sum(1 for ch in reply if _AR_RE.match(ch)) / max(1, len(reply))


def _latin_ratio(reply: str) -> float:
    if not reply:
        return 0.0
    return sum(1 for ch in reply if _LATIN_RE.match(ch)) / max(1, len(reply))


def check_reply(reply: str, seed: dict, seed_shingles: set[str],
                registry: set[str]) -> tuple[str | None, list[str]]:
    """Validate one teacher reply -> (final_content | None, notes).

    Tool-call replies return the (repaired/redumped) JSON string; plain
    replies return text. None = drop (note explains).
    """
    notes: list[str] = []
    text = (reply or "").strip()
    if not text:
        return None, ["empty"]
    if text == seed["seed"].strip():
        return None, ["echo"]
    if _CANARY_RE.search(text):
        return None, ["canary"]
    leak = seed_shingles & _shingles(text)
    if leak:
        return None, ["self-leak"]
    # markdown fences: the teacher fences tool JSON — unwrap before judging
    # so fenced tool calls hit the SAME gates as raw JSON, and fenced prose
    # is judged as prose
    fence = _FENCE_RE.match(text)
    if fence:
        inner = fence.group(1).strip()
        if not inner:
            return None, ["empty"]
        text = inner
        notes.append("unfenced")
    # unwrap a soup final-envelope to plain text (scratch serves plain)
    if text.startswith("{") and '"tool"' in text[:40]:
        unwrapped, action = _unwrap_final(text)
        if action == "malformed":
            return None, ["malformed-json"]
        if action == "plain":
            notes.append("unwrapped-final")
            text = unwrapped
    # tool JSON gate
    tool_match = _TOOL_CALL_RE.match(text)
    if tool_match:
        text2, repair_notes = _repair_tool_names(text, registry)
        notes.extend(repair_notes)
        if any(n.startswith("unknown:") for n in repair_notes):
            return None, repair_notes
        m2 = _TOOL_CALL_RE.match(text2)
        if not m2:
            return None, ["malformed-json"]
        try:
            obj = json.loads(text2)
        except json.JSONDecodeError:
            return None, ["malformed-json"]
        name = obj["tool"]
        required = _tool_required_args().get(name, ())
        args = obj.get("arguments", {}) if isinstance(
            obj.get("arguments"), dict) else {}
        missing = [k for k in required if k not in args]
        if missing:
            return None, [f"schema:missing-{'+'.join(missing)}"]
        text = json.dumps(obj, ensure_ascii=False)
        notes.append("tool-call")
    # kind-aware acceptance: a tool call is only a valid answer for
    # tool/memory/honesty seeds. Chat and capability answers are PLAIN text
    # (the 2026-09-26 run: chat seeds got tool JSON with placeholder args,
    # memory seeds — where the call was right — were dropped by the schema
    # gate because the teacher omitted required args).
    is_tool = "tool-call" in notes
    if is_tool and seed.get("kind") not in _TOOL_CALL_KINDS:
        if seed.get("kind") == "refusal":
            return None, ["refusal-complied"]
        return None, ["tool-for-chat"]
    # refusal seeds: a plain-text answer must actually refuse
    if seed.get("kind") == "refusal" and not is_tool:
        lowered = text.lower()
        if not any(cue in lowered for cue in _REFUSAL_CUES):
            return None, ["refusal:no-cue"]
    # language gate
    if seed["lang"] == "ar" and _arabic_ratio(text) < 0.10:
        return None, ["lang:expected-ar"]
    if seed["lang"] == "en" and (_arabic_ratio(text) > 0.05
                                 or _latin_ratio(text) < 0.20):
        return None, ["lang:expected-en"]
    # budget gate (v2 estimator)
    record = {"messages": [
        {"role": "user", "content": seed["seed"]},
        {"role": "assistant", "content": text},
    ]}
    report = v2.token_report(record)
    if report["answer"] > ANSWER_TOKEN_BUDGET:
        return None, [f"budget:answer={report['answer']}"]
    if report["total"] > TRAIN_MAX_LENGTH:
        return None, [f"budget:total={report['total']}"]
    return text, notes


def build(*, base_url: str = TEACHER_BASE_URL, model: str = TEACHER_MODEL,
          seeds_path: Path = SEEDS, exam_path: Path = EXAM,
          output: Path = OUTPUT, transport=None, max_seeds: int | None = None,
          sleep_fn=time.sleep) -> dict:
    """Ask the teacher for every seed; write rows + report; return report."""
    from agent_loop import _identity_fast

    exam_fps = load_exam_fingerprints(exam_path)
    for seed in load_seeds(seeds_path):
        for variant in (seed["seed"].strip(),
                        _hash_pair(seed["seed"].strip(), "")):
            if variant in exam_fps:
                raise SystemExit(
                    f"EXAM LEAK: seed {seed['id']} shares wording with an "
                    "exam prompt - rewrite the seed, never train on exam text")
    seeds = load_seeds(seeds_path)
    if max_seeds is not None:
        seeds = seeds[:max_seeds]

    registry = _registry_names()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path = output.with_suffix(".report.json")  # travels with output
    report: dict = {
        "teacher": model, "base_url": base_url, "seeds_file": str(seeds_path),
        "seeds": len(seeds), "rows_out": 0, "identity_local": 0,
        "request_failures": 0, "drops": Counter(), "notes": Counter(),
        "arabic_rows": 0, "tool_rows": 0, "started": time.time(),
    }
    rows: list[dict] = []
    for index, seed in enumerate(seeds, 1):
        print(f"[{index}/{len(seeds)}] {seed['id']} ...", flush=True)
        seed_shingles = _shingles(seed["seed"])
        # identity questions NEVER reach the teacher: whenever the
        # deterministic fast-path matches, its card IS the answer (the
        # owner's identity-truth rule — a 7B soup brain once answered
        # "trained by Alibaba Cloud").
        card = _identity_card(seed["seed"])
        if card is not None:
            reply, source = card, "identity"
        elif seed["kind"] == "identity":
            report["drops"]["identity-unverified-trigger"] += 1
            print("    dropped: identity seed not verified by "
                  "_identity_fast - fix the seed wording", flush=True)
            continue
        else:
            prompt = _render_scratch_prompt(seed["seed"])
            reply = None
            source = "teacher"
            for attempt in range(MAX_RETRIES + 1):
                try:
                    reply = ask_teacher(prompt, base_url=base_url,
                                        model=model, transport=transport)
                    break
                except Exception as exc:  # noqa: BLE001 - teacher down/hiccup
                    if attempt >= MAX_RETRIES:
                        report["request_failures"] += 1
                        print(f"    request failed: {exc}", flush=True)
                    else:
                        if sleep_fn is not None:
                            sleep_fn(1.0)
            if reply is None:
                continue
        content, notes = check_reply(reply, seed, seed_shingles, registry)
        if content is None:
            for note in notes:
                report["drops"][note.split(":")[0]] += 1
            print(f"    dropped: {notes}", flush=True)
            continue
        rows.append({
            "messages": [
                {"role": "user", "content": seed["seed"]},
                {"role": "assistant", "content": content},
            ],
            "source": f"distill:{seed['id']}:{source}",
        })
        report["rows_out"] += 1
        if seed["lang"] == "ar":
            report["arabic_rows"] += 1
        if "tool-call" in notes:
            report["tool_rows"] += 1
        if source == "identity":
            report["identity_local"] += 1
        for note in notes:
            report["notes"][note] += 1
        print(f"    ok ({len(content)} chars)"
              + (f" {notes}" if notes else ""), flush=True)
        if sleep_fn is not None and source == "teacher":
            sleep_fn(REQUEST_GAP_S)

    output.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")
    report["elapsed_s"] = round(time.time() - report.pop("started"), 1)
    report = {k: (dict(v) if isinstance(v, Counter) else v)
              for k, v in report.items()}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"wrote {report['rows_out']} rows -> {output}", flush=True)
    return report


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    report = build()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["rows_out"] >= 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
