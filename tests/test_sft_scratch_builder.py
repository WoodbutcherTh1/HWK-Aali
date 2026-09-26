"""Tests for the run-8 scratch-shape SFT mix builder (build_aali_sft_scratch).

Run-7 wall: the mix taught SOUP-shaped rows (soup system line + {"tool":
"final", ...} envelopes) while Phase C serves the SCRATCH transcript. These
tests pin the conversion contract: no system messages, finals unwrapped to
plain text, tool JSON kept with REGISTRY names only, budget/structural gates
honest, and converted records render through train_scratch._sft_record_text
into exactly the shape agent_loop._local_model_loop serves.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "file-agent"))

import build_aali_sft_scratch as builder  # noqa: E402
import train_scratch  # noqa: E402

SOUP_SYSTEM = ('You are Aali. For tools reply {"tool": name, "arguments": {...}}. '
               'For the final answer use {"tool": "final", "content": reply}. '
               "Answer in the user's language and be honest.")


def soup_record(user: str, final_json: str, source: str = "sft_mix") -> dict:
    return {"messages": [
        {"role": "system", "content": SOUP_SYSTEM},
        {"role": "user", "content": user},
        {"role": "assistant", "content": final_json},
    ], "source": source}


def final_envelope(text: str) -> str:
    return json.dumps({"tool": "final", "content": text})


@pytest.fixture()
def registry() -> set[str]:
    return builder._registry_names()


# --- final unwrapping ------------------------------------------------------

def test_final_json_unwrapped_to_plain_text(registry) -> None:
    record = soup_record("hi", final_envelope("Hello there!"))
    converted, notes = builder.convert_record(record, registry)
    assert converted is not None
    assert converted["messages"][-1]["content"] == "Hello there!"
    assert "unwrapped-final" in notes


def test_no_system_messages_survive(registry) -> None:
    converted, _ = builder.convert_record(
        soup_record("hi", final_envelope("hey")), registry)
    assert converted is not None
    assert all(m["role"] != "system" for m in converted["messages"])


def test_nested_quotes_survive_unwrap(registry) -> None:
    answer = 'Encrypted string: "Khoor Zruog"'
    converted, _ = builder.convert_record(
        soup_record("caesar it", final_envelope(answer)), registry)
    assert converted is not None
    assert converted["messages"][-1]["content"] == answer


def test_plain_completion_untouched(registry) -> None:
    converted, notes = builder.convert_record(
        soup_record("hi", "Just a plain answer."), registry)
    assert converted is not None
    assert converted["messages"][-1]["content"] == "Just a plain answer."
    assert not any(n == "unwrapped-final" for n in notes)


def test_multi_turn_mid_final_also_unwrapped(registry) -> None:
    record = {"messages": [
        {"role": "system", "content": SOUP_SYSTEM},
        {"role": "user", "content": "step one?"},
        {"role": "assistant", "content": final_envelope("did step one")},
        {"role": "user", "content": "step two?"},
        {"role": "assistant", "content": final_envelope("did step two")},
    ], "source": "sft_mix"}
    converted, notes = builder.convert_record(record, registry)
    assert converted is not None
    assert notes.count("unwrapped-final") == 2
    assert converted["messages"][1]["content"] == "did step one"
    assert converted["messages"][-1]["content"] == "did step two"


# --- tool calls ------------------------------------------------------------

def test_tool_json_kept_with_registry_name(registry) -> None:
    call = json.dumps({"tool": "memory", "arguments":
                       {"action": "save", "text": "never push without approval"}})
    converted, _ = builder.convert_record(soup_record("remember this", call), registry)
    assert converted is not None
    obj = json.loads(converted["messages"][-1]["content"])
    assert obj["tool"] == "memory"
    assert obj["tool"] in registry


def test_invented_tool_name_repaired_via_alias(registry) -> None:
    call = json.dumps({"tool": "paint",
                       "arguments": {"prompt": "a cat on the moon"}})
    converted, notes = builder.convert_record(soup_record("draw", call), registry)
    assert converted is not None
    obj = json.loads(converted["messages"][-1]["content"])
    assert obj["tool"] == "generate_image"
    assert any(n.startswith("alias:paint->") for n in notes)


def test_unknown_tool_name_drops_row(registry) -> None:
    call = json.dumps({"tool": "warp_drive", "arguments": {"speed": 9}})
    converted, notes = builder.convert_record(soup_record("go", call), registry)
    assert converted is None
    assert any(n.startswith("unknown:warp_drive") for n in notes)


def test_malformed_protocol_json_dropped(registry) -> None:
    # the exact soup-taught shape: UNQUOTED tool name, unparseable JSON
    converted, notes = builder.convert_record(
        soup_record("hi", '{"tool": name, "arguments": {...}}'), registry)
    assert converted is None
    assert any(n.startswith("malformed") for n in notes)


# --- structural + budget gates ---------------------------------------------

def test_user_only_record_dropped(registry) -> None:
    record = {"messages": [{"role": "user", "content": "anyone there?"}]}
    converted, notes = builder.convert_record(record, registry)
    assert converted is None
    assert any("structure" in n for n in notes)


def test_oversized_answer_dropped_not_truncated(registry) -> None:
    big = "x" * (builder.ANSWER_TOKEN_BUDGET * 5)
    converted, notes = builder.convert_record(
        soup_record("write", final_envelope(big)), registry)
    assert converted is None
    assert any(n.startswith("budget:answer=") for n in notes)


def test_source_tagged_scratch(registry) -> None:
    converted, _ = builder.convert_record(
        soup_record("hi", final_envelope("hey")), registry)
    assert converted is not None
    assert converted["source"].endswith("+scratch")


# --- the REAL contract: rendered row shape == serving shape -----------------

def test_converted_row_renders_into_serving_transcript(registry) -> None:
    converted, _ = builder.convert_record(
        soup_record("hi", final_envelope("Hello there!")), registry)
    row = train_scratch._sft_record_text(converted)
    # the trainer's own rendering: System line, tools, turns, instruction
    assert row.startswith("System: You are Aali.\n")
    assert "Available tools: " in row
    assert row.endswith("Assistant: Hello there!")
    # the completion is the PLAIN answer - soup's final envelope never renders
    assert train_scratch._sft_completion_text(converted) == "Hello there!"


def test_converted_row_carries_serving_instruction_not_prompt_text(registry) -> None:
    converted, _ = builder.convert_record(
        soup_record("hi", final_envelope("ok")), registry)
    prompt = train_scratch._sft_prompt_text(converted)
    assert prompt.endswith(train_scratch.SCRATCH_INSTRUCTION)
    # shape, not prompt text: the big SYSTEM_PROMPT never enters training rows
    assert "INTEGRITY MARKS" not in prompt
    assert len(prompt) < 4000  # boilerplate stays small next to a 4096 ctx


def test_converted_tool_call_renders_after_anchor(registry) -> None:
    call = json.dumps({"tool": "generate_image",
                       "arguments": {"prompt": "dunes at dawn"}})
    converted, _ = builder.convert_record(soup_record("draw", call), registry)
    row = train_scratch._sft_record_text(converted)
    assert row.endswith("Assistant: " + call)


# --- end-to-end build --------------------------------------------------------

def test_build_end_to_end_writes_output_and_report(tmp_path) -> None:
    source = tmp_path / "in.jsonl"
    output = tmp_path / "out.jsonl"
    records = [
        soup_record("q1", final_envelope("a1")),
        soup_record("q2", json.dumps({"tool": "read_file",
                                      "arguments": {"path": "x.txt"}})),
        soup_record("q3", json.dumps({"tool": "paint",
                                      "arguments": {"prompt": "dune"}})),
        {"messages": [{"role": "user", "content": "lonely"}]},  # dropped
    ]
    source.write_text("".join(json.dumps(r) + "\n" for r in records),
                      encoding="utf-8")
    report = builder.build(source, output)
    lines = output.read_text(encoding="utf-8").splitlines()
    assert report["rows_in"] == 4
    assert report["rows_out"] == 3
    assert len(lines) == 3
    assert report["final_unwrapped"] == 1
    assert any("alias" in k for k in report["name_repairs"])
    assert report["drops"].get("structure") == 1
    for line in lines:
        record = json.loads(line)
        assert all(m["role"] != "system" for m in record["messages"])
        assert record["messages"][-1]["role"] == "assistant"


# --- real tokenizer smoke (local file; skipped where it is absent) ----------

TOKENIZER = Path("D:/hwk-data/tokenizer/hwk_spm.model")


@pytest.mark.skipif(not TOKENIZER.exists(), reason="local tokenizer file")
def test_converted_row_encodes_under_the_real_tokenizer(registry) -> None:
    from hwk_model.bpe_tokenizer import load_bpe
    tokenizer = load_bpe(str(TOKENIZER))
    converted, _ = builder.convert_record(
        soup_record("سوي لي مقطع لشلال بالموس",
                    final_envelope("سأستخدم أداة الفيديو الآن.")), registry)
    ids = tokenizer.encode(train_scratch._sft_record_text(converted))
    assert len(ids) < 4096  # survives the trainer window whole
