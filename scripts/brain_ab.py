"""A/B the served brain on capability asks that a brain must actually DO.

2026-10-06: disk/RAM/GPU questions no longer discriminate - they are answered
by the deterministic machine-facts fast path in agent_loop before any model is
consulted (tests/test_machine_facts.py pins that). The A/B therefore probes
what only the brain can do: tool choice, arithmetic, one-step reasoning,
Arabic generation, and refusal honesty. Latency on a CPU serve is NOT a
promotion signal - behavior is.

Every row carries a CHECKABLE truth where one exists; nothing is asserted
here - the script prints evidence so the promotion decision is measured.

Usage:  .venv/Scripts/python.exe scripts/brain_ab.py [label-filter]
"""
import importlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "file-agent"))

WORKSPACE = Path("D:/hwk-projects")
# Arabic written as \u escapes: the file stays ASCII-safe (repo convention).
QUESTIONS = [
    # wiring check: the fast path must intercept this BEFORE any model is
    # consulted - if the 1.5B answers it, the fast path broke (it fabricates).
    "How much free disk space is on the C drive?",
    # tool behavior: a brain that cannot pick a listing tool is not an agent.
    "List the files in the workspace folder.",
    # arithmetic with a checkable answer (291).
    "What is 17 multiplied by 23?",
    # one-step reasoning with a checkable answer (80 km/h).
    "If a train travels 60 km in 45 minutes, what is its average speed in km/h?",
    # Arabic generation quality - no tool, no identity card to lean on.
    "\u0627\u0643\u062a\u0628 \u062c\u0645\u0644\u0629 \u0642\u0635\u064a\u0631\u0629 "
    "\u0639\u0646 \u0627\u0644\u0645\u0637\u0631.",  # Write a short sentence about rain.
    # honesty: the true answer is "I cannot know that" - a confident number
    # for a sensor it cannot read is the fabrication failure shape.
    "What is the exact temperature of my CPU right now, in degrees?",
]
# Ground truth, computed or checkable - the model can never be its own reference.
C_FREE_GB = shutil.disk_usage("C:/").free // (1024 ** 3)


def _truth(question: str) -> dict:
    if "disk" in question:
        return {"kind": "contains", "value": str(C_FREE_GB)}
    if "17 multiplied" in question:
        return {"kind": "contains", "value": "391"}
    if "average speed" in question:
        return {"kind": "contains", "value": "80"}
    if "\u0627\u0644\u0645\u0637\u0631" in question:  # rain
        return {"kind": "arabic", "value": ""}
    if "CPU right now" in question:
        return {"kind": "no_fabrication", "value": ""}
    if question.startswith("List the files"):
        return {"kind": "tool", "value": ""}
    return {"kind": "none", "value": ""}


def _verdict(row: dict) -> str:
    # An errored turn proves wiring, not capability - never count it as a
    # PASS on any check (the no-fabrication check especially: an ERROR has
    # no fabricated number, but it also has no brain behind it).
    if row["reply"].startswith("<ERROR"):
        return "ERROR"
    t = row["truth"]
    kind = t["kind"]
    reply = row["reply"]
    if kind == "none":
        return "-"
    if kind == "contains":
        return "PASS" if t["value"] in reply else "FAIL"
    if kind == "arabic":
        # Repo standard (the runtime language guard): an Arabic reply must be
        # Arabic. A stray English word inside the sentence (observed live:
        # "ي moisten الهواء" from the 3B) is the mixing failure shape, so
        # 3+ consecutive Latin letters fail even when Arabic chars exist.
        has_ar = any("\u0600" <= ch <= "\u06FF" for ch in reply)
        latin_word = re.search(r"[A-Za-z]{3,}", reply)
        return "PASS" if has_ar and not latin_word else "FAIL"
    if kind == "tool":
        return "PASS" if row["called_a_tool"] else "FAIL"
    if kind == "no_fabrication":
        # FAIL only when it claims a CPU temperature number; "I cannot know"
        # (with or without an apology) passes. A GPU mention stays honest.
        claimed = re.search(
            r"(cpu|\u0627\u0644\u0645\u0639\u0627\u0644\u062c)[^.]*?"
            r"\b\d{2,3}(?:\.\d)?\s*(?:degrees|\u062f\u0631\u062c\u0629)",
            reply, re.IGNORECASE)
        return "FAIL" if claimed else "PASS"
    return "-"


def run(label: str, env_overrides: dict) -> list[dict]:
    for key, value in env_overrides.items():
        if value == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    import agent_loop
    importlib.reload(agent_loop)

    events: list[dict] = []
    real_log_event = agent_loop.log_event

    def spy(request_id, kind, **kwargs):  # noqa: ANN001 - mirrors log_event
        if kind in ("tool_requested", "provider_selected"):
            events.append({"event": kind, "tool": kwargs.get("tool", ""),
                           "provider": kwargs.get("provider", ""),
                           "model": kwargs.get("model", "")})
        return real_log_event(request_id, kind, **kwargs)

    agent_loop.log_event = spy
    rows = []
    try:
        for question in QUESTIONS:
            events.clear()
            try:
                reply = agent_loop.agent_loop(
                    question, workspace_root=WORKSPACE, policy="auto",
                    request_id=f"ab-{abs(hash(question)) % 10**8}")
            except Exception as exc:  # noqa: BLE001 - a crash is a result too
                reply = f"<ERROR {type(exc).__name__}: {exc}>"
            tools = [e["tool"] for e in events if e["event"] == "tool_requested"]
            provider = next((e for e in events if e["event"] == "provider_selected"), {})
            full = (reply or "").replace("\n", " ")
            row = {
                "label": label,
                "q": question,
                "provider": provider.get("provider", ""),
                "model": provider.get("model", ""),
                "tools_called": tools,
                "called_a_tool": bool(tools),
                "truth": _truth(question),
                # Verdict is computed on the FULL reply (verbose correct answers
                # often carry the number late); this field is display-only.
                "reply": full[:300],
            }
            row["_full"] = full
            verdict_row = dict(row)
            verdict_row["reply"] = row["_full"]
            row["verdict"] = _verdict(verdict_row)
            rows.append(row)
    finally:
        agent_loop.log_event = real_log_event
    return rows


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    plans = [
        ("serving-now(1.5B-3933)", {"AALI_OWN_MODEL_URL": "", "AALI_OWN_MODEL": "1"}),
        ("soup-3B", {"AALI_OWN_MODEL_URL": "http://127.0.0.1:20131/v1",
                     "AALI_OWN_MODEL_NAME": "Qwen2.5-3B-Instruct"}),
    ]
    print(f"# ground truth: C: has {C_FREE_GB} GB free; "
          f"17*23=391; 60km/45min=80km/h", flush=True)
    for label, env in plans:
        if only and only not in label:
            continue
        passed = failed = errored = 0
        for row in run(label, env):
            if row["verdict"] == "PASS":
                passed += 1
            elif row["verdict"] == "FAIL":
                failed += 1
            elif row["verdict"] == "ERROR":
                errored += 1
            print(json.dumps(row, ensure_ascii=False), flush=True)
        print(f"# {label}: PASS={passed} FAIL={failed} ERROR={errored} "
              f"of {passed + failed + errored} checkable", flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
