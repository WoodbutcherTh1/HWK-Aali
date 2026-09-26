"""Build sft_scratch.jsonl: the SFT mix in Aali's SCRATCH serving shape.

Run-7 diagnosis (2026-09-26, tasks/phase-c-own-brain-sft-relaunch.md): the
model was taught rows shaped like the SOUP serving template and then probed
in the SCRATCH transcript shape it never saw. Every sft_v2.jsonl row carries
a soup protocol system message ("You are Aali. For tools reply {\"tool\": name,
...} For the final answer use {\"tool\": \"final\", ...}" — note the UNQUOTED
`name`, malformed JSON taught as the protocol), and 66% of completions wrap
plain answers in a soup-only `{"tool":"final","content":...}` envelope that
the scratch serving path would render to the user RAW.

This builder converts the SAME content into the SCRATCH row contract
(train_scratch._sft_record_text renders a messages record as:
  System: You are Aali.
  Available tools: <live registry names>
  User: ...
  Assistant: [instruction]  <- appended by the trainer
and the final assistant turn is the completion):

  1. DROP every system message (the trainer prepends SCRATCH_SYSTEM_LINE;
     a record system message would become a SECOND "System:" turn).
  2. UNWRAP final-JSON: `{"tool":"final","content":X}` -> plain X, at every
     assistant position — the scratch protocol speaks plain text for answers;
     soup's final-envelope never reaches a scratch user.
  3. REPAIR invented tool names through file_agent.tool_guard.ALIASES and
     REFUSE (drop the row) any tool call whose name is neither a live
     registry name (file_tools._FUNCTIONS) nor alias-resolvable — the run-7
     mix taught `{"tool": name}`-style names the registry never had.
  4. Token budget: answer <= ANSWER_TOKEN_BUDGET, total <= TRAIN_MAX_LENGTH
     (estimator imported from build_aali_sft_v2; constants are ours —
     the scratch row carries tools + instruction boilerplate too).
  5. Structural gate: ends with an assistant turn; no empty turns.

Output: D:/hwk-data/soup/sft_scratch.jsonl (+ .report.json). The file is
consumed by the Phase C trainer via AALI_PHASE_C_DATA — it never touches the
SOUP pipeline's sft_v2.jsonl (the soup path keeps its own template; KB-chunk
isolation lesson from projects: separate files for separate consumers).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "file-agent"))

import build_aali_sft_v2 as v2  # noqa: E402  (estimator only)

SOURCE = Path("D:/hwk-data/soup/sft_v2.jsonl")
OUTPUT = Path("D:/hwk-data/soup/sft_scratch.jsonl")
REPORT = OUTPUT.with_suffix(".report.json")

# Scratch rows ride in a 4096 ctx with tools + instruction boilerplate
# (SCRATCH_SYSTEM_LINE + tool list + SCRATCH_INSTRUCTION ~ 90 tokens at the
# v2 estimator's rates, plus the System/System prompt diff at serve time).
# Same answer budget as v2; the total gets headroom for the scratch prefix.
ANSWER_TOKEN_BUDGET = 200
TRAIN_MAX_LENGTH = 900
_PROMPT_BUDGET = TRAIN_MAX_LENGTH - ANSWER_TOKEN_BUDGET

_TOOL_RE = re.compile(r"^\s*\{\s*[\"']tool[\"']\s*:")
# first {"tool": "name"} object inside a wrapped-content string (or any turn)
_TOOL_CALL_RE = re.compile(
    r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*')
_FINAL_RE = re.compile(
    r'^\s*\{\s*"tool"\s*:\s*["\']final["\']\s*,\s*["\']content["\']\s*:\s*(.*)\}\s*$',
    re.DOTALL)


def _live_registry() -> set[str]:
    from file_agent.file_tools import _FUNCTIONS
    return set(_FUNCTIONS)


def _registry_names() -> set[str]:
    """Registry + alias targets that exist in the registry."""
    from file_agent.tool_guard import ALIASES
    return _live_registry() | {
        target for target in ALIASES.values() if target in _live_registry()
    }


def _unwrap_final(content: str) -> tuple[str, str]:
    """Unwrap one soup final-envelope. Returns (new_content, action).

    action: "plain" (was final-JSON, now unwrapped), "tool" (legit tool
    call kept as JSON), "text" (already plain), "malformed" (unparseable
    JSON-shaped text — caller drops the turn's row).
    """
    text = content.strip()
    if not _TOOL_RE.match(text):
        return text, "text"
    m = _FINAL_RE.match(text)
    if m:
        inner = m.group(1).strip()
        # strip ONE level of JSON string quoting if present
        if inner.startswith('"') and inner.endswith('"'):
            try:
                inner = json.loads(inner)
                if not isinstance(inner, str):
                    return text, "malformed"
            except json.JSONDecodeError:
                pass  # keep raw inner text; the estimator + row gate judge it
        return str(inner).strip(), "plain"
    name_m = _TOOL_CALL_RE.match(text)
    if name_m:
        return text, "tool"
    return text, "malformed"


def _repair_tool_names(content: str, registry: set[str]) -> tuple[str, list[str]]:
    """Rewrite invented tool names to registry names inside tool-call JSON.

    Returns (repaired_content, [notes]). Unresolvable names are reported so
    the caller drops the row — the run-7 mix taught names the registry
    never had (tool_guard ALIASES + fuzzy are the repair tables). The JSON
    object is PARSED and re-dumped (never string-replaced): spacing varies
    across sources and a silent no-op replace would train the invented name.
    """
    notes: list[str] = []
    m = _TOOL_CALL_RE.match(content.strip())
    if not m:
        return content, notes
    try:
        obj = json.loads(content.strip())
    except json.JSONDecodeError:
        return content, ["unknown:unparseable-tool-json"]
    if not isinstance(obj, dict) or not isinstance(obj.get("tool"), str):
        return content, ["unknown:tool-not-a-string"]
    name = obj["tool"]
    if name in registry:
        return content, notes
    from file_agent.tool_guard import ALIASES
    folded = name.strip().lower()
    if folded in ALIASES and ALIASES[folded] in registry:
        obj["tool"] = ALIASES[folded]
        notes.append(f"alias:{name}->{ALIASES[folded]}")
        return json.dumps(obj, ensure_ascii=False), notes
    import difflib
    close = difflib.get_close_matches(folded, registry, n=2, cutoff=0.72)
    if len(close) == 1:
        obj["tool"] = close[0]
        notes.append(f"fuzzy:{name}->{close[0]}")
        return json.dumps(obj, ensure_ascii=False), notes
    notes.append(f"unknown:{name}")
    return content, notes


def convert_record(record: dict, registry: set[str]) -> tuple[dict | None, list[str]]:
    """Convert one sft_v2 record to scratch shape. (None, notes) = drop."""
    notes: list[str] = []
    messages = record.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return None, ["structure:not-messages"]
    # 1. drop soup system messages (the trainer prepends its own System line)
    out = [dict(m, content=str(m.get("content", "")))
           for m in messages if m.get("role") != "system"]
    if not out or out[-1].get("role") != "assistant":
        return None, ["structure:no-assistant-final"]
    # 2/3. unwrap finals + repair names at EVERY assistant position
    kept: list[dict] = []
    for m in out:
        role = m.get("role")
        content = m.get("content", "")
        if role == "assistant":
            content, action = _unwrap_final(content)
            if action == "plain":
                notes.append("unwrapped-final")
            if action == "malformed":
                return None, [f"malformed-json:{content[:40]}"]
            if action == "tool":
                content, repair_notes = _repair_tool_names(content, registry)
                notes.extend(repair_notes)
                if any(n.startswith("unknown:") for n in repair_notes):
                    return None, repair_notes
        if not content.strip():
            return None, [f"empty:{role}"]
        kept.append({"role": role, "content": content})
    if kept[-1]["role"] != "assistant":
        return None, ["structure:no-assistant-final"]
    converted = {"messages": kept,
                 "source": str(record.get("source", "?")) + "+scratch"}
    # 4. token budget (v2 estimator over the converted content)
    report = v2.token_report(converted)
    if report["answer"] > ANSWER_TOKEN_BUDGET:
        return None, [f"budget:answer={report['answer']}"]
    if report["total"] > TRAIN_MAX_LENGTH:
        return None, [f"budget:total={report['total']}"]
    # 5. language share of the completion (report only — no filtering)
    return converted, notes


def build(source: Path = SOURCE, output: Path = OUTPUT) -> dict:
    """Convert the whole mix; write output + report; return the report dict."""
    registry = _registry_names()
    rows: list[dict] = []
    report: dict = {
        "source": str(source), "output": str(output),
        "rows_in": 0, "rows_out": 0,
        "actions": Counter(), "drops": Counter(),
        "arabic_completions": 0, "tool_call_completions": 0,
        "final_unwrapped": 0, "name_repairs": Counter(),
    }
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        report["rows_in"] += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            report["drops"]["bad-json"] += 1
            continue
        converted, notes = convert_record(record, registry)
        for note in notes:
            if note.startswith(("alias:", "fuzzy:")):
                report["name_repairs"][note] += 1
            elif note == "unwrapped-final":
                report["final_unwrapped"] += 1
        if converted is None:
            for note in notes:
                report["drops"][note.split(":")[0]] += 1
            continue
        rows.append(converted)
        report["rows_out"] += 1
        last = converted["messages"][-1]["content"]
        if _TOOL_RE.match(last):
            report["actions"]["tool-json"] += 1
            report["tool_call_completions"] += 1
        else:
            report["actions"]["plain"] += 1
        if any("\u0600" <= ch <= "\u06ff" for ch in last):
            report["arabic_completions"] += 1

    output.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")
    report = {k: (dict(v) if isinstance(v, Counter) else v)
              for k, v in report.items()}
    report["arabic_share"] = round(
        report["arabic_completions"] / max(1, report["rows_out"]), 3)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return report


def main() -> int:
    print(f"building {OUTPUT} from {SOURCE}")
    rep = build()
    print(f"rows: {rep['rows_in']} in -> {rep['rows_out']} out "
          f"(arabic share {rep['arabic_share']})")
    print(f"completions: {rep['actions']}")
    print(f"drops: {rep['drops']}")
    print(f"name repairs: {rep['name_repairs']}")
    return 0 if rep["rows_out"] > 1000 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
