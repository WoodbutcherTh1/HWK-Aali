"""Machine-facts fast path: the numbers come from the OS, not the model.

Owner symptom (2026-10-06): asked how much free disk space was on C:, the
served 1.5B answered 10 GB, then 1.2 GB, then 2 GB across three runs, while
the truth was 57 GB, and never emitted a single tool call. Every client
(phone, web, desktop) goes through that one brain, so a fabricated machine
number reads as "Aali did nothing".

These tests pin the contract: real values, Arabic-first, bilingual, narrow
triggering (ordinary chat must NOT be hijacked), and no memory-tool confusion.
"""
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "file-agent"))

import agent_loop  # noqa: E402


def _truth(drive: str = "C") -> int:
    return shutil.disk_usage(f"{drive}:/").free // (1024 ** 3)


def _has_gb_near(text: str, drive: str = "C", tol: float = 1.0) -> bool:
    """The answer carries the drive's real free size, tolerant to rounding.

    The feature renders a rounded GiB decimal (52.9) while a floor read of the
    same moment gives the integer (52); a live C: crossing a GiB boundary
    between the answer's read and the test's read makes floor and round
    disagree by exactly 1, so a substring check flakes by construction
    (observed 2026-10-06: 53.0 vs 52 within minutes). A fabricated number
    (10 GB, 1.2 GB, 2 GB — the owner's real symptoms) still fails: nothing
    in the text lands near the truth.
    """
    truth = _truth(drive)
    for token in re.findall(r"\d+(?:\.\d+)?", text):
        try:
            if abs(float(token) - truth) <= tol:
                return True
        except ValueError:  # pragma: no cover - regex only yields parseable numbers
            continue
    return False


def test_disk_answer_is_the_real_number_not_a_guess():
    answer = agent_loop._machine_facts_fast("How much free disk space is on the C drive?")
    assert answer, "a machine-facts question must be answered deterministically"
    assert _has_gb_near(answer, "C"), \
        f"answer must carry the real free GB (~{_truth('C')}): {answer}"
    assert "could not be read" not in answer


def test_disk_facts_read_every_drive_when_none_is_named():
    facts = agent_loop._machine_facts("how much space is left on this machine")
    assert facts is not None and facts["group"] == "disk"
    assert set(facts["facts"]) == {"C", "D", "X"}
    for drive, info in facts["facts"].items():
        assert set(info) == {"free_gb", "total_gb", "used_pct"}, info
        assert 0 <= info["used_pct"] <= 100


def test_named_drive_narrows_the_answer():
    facts = agent_loop._machine_facts("free space on D:")
    assert facts is not None
    assert set(facts["facts"]) == {"D"}


def test_arabic_disk_question_is_answered_in_arabic():
    # The owner's own wording from the live session.
    answer = agent_loop._machine_facts_fast(
        "\u0643\u0645 \u0645\u0633\u0627\u062d\u0629 \u0627\u0644\u0642\u0631\u0635 "
        "\u0627\u0644\u0645\u062a\u0628\u0642\u064a\u0629 \u0639\u0644\u0649 "
        "\u0627\u0644\u062d\u0627\u0633\u0648\u0628\u061f")
    assert answer, "the Arabic phrasing must hit the fast path too"
    assert any("\u0600" <= ch <= "\u06FF" for ch in answer)
    assert _has_gb_near(answer, "C"), answer


def test_ram_and_gpu_answers_are_real():
    ram = agent_loop._machine_facts_fast("how much RAM does this machine have?")
    assert ram and ("used" in ram or "total_gb" in str(ram))
    gpu = agent_loop._machine_facts_fast("what GPU is this?")
    assert gpu and ("MiB" in gpu or "could not" in gpu)


def test_memory_tool_request_is_not_a_ram_question():
    # Aali's persistent memory, not the machine's RAM.
    assert agent_loop._machine_facts_fast("save this to your memory") is None
    assert agent_loop._machine_facts_fast("what did I tell you to remember?") is None
    assert agent_loop._machine_facts_fast(
        "\u0627\u062d\u0641\u0638 \u0647\u0630\u0627 \u0641\u064a \u0630\u0627\u0643\u0631\u062a\u0643") is None


def test_ordinary_chat_is_never_hijacked():
    for message in ("hi", "who are you?", "what can you do?",
                    "explain how neural networks work",
                    "write me a python script that sorts a list",
                    "what is the weather in Amman"):
        assert agent_loop._machine_facts_fast(message) is None, message


def test_ambiguous_two_group_question_is_not_answered():
    # "disk and RAM" spans two groups -> the model may clarify instead.
    assert agent_loop._machine_facts_fast("how much disk space and RAM do I have?") is None


class _NoHTTP:
    """Any outbound HTTP is a model consult — the contract forbids it here."""

    def __getattr__(self, _name):
        def explode(*args, **kwargs):  # noqa: ANN001
            raise AssertionError("the model must not be consulted for a machine fact")

        return explode


def test_agent_loop_uses_the_fast_path_without_calling_the_model(monkeypatch):
    """End-to-end at the entry point the clients actually use.

    The guard is the HTTP transport, NOT _openai_compat_loop: that function is
    where the fast path lives (next to _identity_fast), so replacing it would
    delete the very code under test and pass/fail for the wrong reason.
    """
    monkeypatch.setattr(agent_loop, "requests", _NoHTTP())
    answer = agent_loop.agent_loop(
        "How much free disk space is on the C drive?",
        workspace_root=Path("D:/hwk-projects"))
    assert _has_gb_near(answer, "C"), answer


def test_local_tool_call_path_returns_the_facts_as_a_final_turn():
    action, arguments = agent_loop._local_tool_call("how much free space on C:")
    assert action == "final"
    assert _has_gb_near(arguments["content"], "C"), arguments