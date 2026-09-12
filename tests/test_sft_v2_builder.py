"""Tests for the sft_v2 builder fixes found in the 2026-09-10 audit.

The audit found three silent bugs starving the graduation re-SFT:
  1. ALL mentor episodes died at write time (over MAX_TOTAL_CHARS) - fixed by
     trim_to_budget (head + tail, tool JSON never split mid-object).
  2. The x3 failure upweight defeated itself - byte-identical copies were
     dropped by the dedup gate before the upweight could matter.
  3. Mentor/chat transcripts leaked foreign toolsets (Read/Edit/Bash/...),
     malformed tool JSON, and bare {"content": ...} / "{}" replies.
  4. (2026-09-12 night) Three soup-train attempts died on the same shuffled
     row: Arabic costs ~1.5 chars/token vs ~4.0 English on the Qwen2.5
     tokenizer, so the flat 2200-char cap let Arabic prompts reach ~950
     tokens and soup refuses rows whose prompt fills data.max_length. Fixed
     with token budgets (TRAIN_MAX_LENGTH/PROMPT/ANSWER) enforced at trim
     AND write time, plus a pipeline-side tripwire (token_overflow_rows).

CPU-only: no GPU, no network, external files redirected to tmp dirs. Run:
    .venv/Scripts/python -m pytest tests/test_sft_v2_builder.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_aali_sft_v2 as builder  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def _long_filler(n_pairs: int, size: int = 80) -> list[dict]:
    filler = "x" * size
    messages: list[dict] = []
    for _ in range(n_pairs):
        messages.append(_msg("user", f"step: {filler}"))
        messages.append(_msg("assistant", f"did step: {filler}"))
    return messages


def _tool_call(name: str, arguments: dict) -> str:
    return json.dumps({"tool": name, "arguments": arguments}, ensure_ascii=False)


def _final(text: str) -> str:
    return json.dumps({"tool": "final", "content": text}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# trim_to_budget: head + tail, JSON never split
# ---------------------------------------------------------------------------

def test_trim_to_budget_keeps_task_head_and_recovery_tail() -> None:
    task = "Fix the failing build in app.py and summarize what was wrong."
    recovery = _tool_call("read_file", {"path": "app.py"})
    messages = (
        [_msg("system", "sys" * 20), _msg("user", task)]
        + _long_filler(30)
        + [_msg("user", "Tool result: it works now"), _msg("assistant", recovery)]
    )
    record = {"messages": messages, "source": "mentor"}

    trimmed = builder.trim_to_budget(record)

    assert trimmed is not None
    result = trimmed["messages"]
    total = sum(len(m.get("content", "")) for m in result)
    assert total <= builder.MAX_TOTAL_CHARS
    # head: the task user turn survives
    assert task in [m.get("content") for m in result]
    # tail: the recovery/final assistant turn survives UNTRUNCATED
    assert recovery in [m.get("content") for m in result]
    # the imitation target must be last
    assert result[-1]["role"] == "assistant"
    assert result[-1]["content"] == recovery


def test_trim_to_budget_never_splits_tool_json() -> None:
    huge_json = _tool_call("write_file", {"path": "big.txt",
                                          "content": "y" * 3000})
    messages = (
        [_msg("system", "sys" * 20), _msg("user", "write the file")]
        + [_msg("user", "here is the content" + "z" * 100),
           _msg("assistant", huge_json)]
    )
    record = {"messages": messages, "source": "mentor"}

    trimmed = builder.trim_to_budget(record)

    # The oversized JSON turn is skipped whole (or the whole record is
    # dropped) - a truncated JSON prefix must never reach the training file.
    if trimmed is not None:
        for m in trimmed["messages"]:
            content = m.get("content", "")
            assert not content.startswith(huge_json[:50]) or content == huge_json


def test_trim_to_budget_leaves_short_records_unchanged() -> None:
    record = {"messages": [_msg("user", "hi"), _msg("assistant", _final("hey"))],
              "source": "mentor"}
    assert builder.trim_to_budget(record) is record


# ---------------------------------------------------------------------------
# upweight vs dedup: failure x3 copies survive, real duplicates do not
# ---------------------------------------------------------------------------

def _write_messages_file(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")


def test_upweight_copies_survive_dedup_but_real_duplicates_do_not(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # one genuinely duplicated pair across two sources -> dedup must keep 1
    duplicate_pair = {"messages": [_msg("user", "what is 2+2?"),
                                   _msg("assistant", _final("4"))],
                      "source": "sft_mix"}
    monkeypatch.setattr(builder, "DEFAULT_SFT_MIX", tmp_path / "sft_mix.jsonl")
    _write_messages_file(builder.DEFAULT_SFT_MIX, [duplicate_pair, dict(duplicate_pair)])

    # one failure episode -> must land x3 (mentor-failure#1/#2/#3)
    failure = {"messages": [
        _msg("user", "deploy the service now"),
        _msg("assistant", _tool_call("run_cmd", {"command": "deploy.sh"})),
        _msg("user", "Tool result: Traceback (most recent call last): crash"),
        _msg("assistant", _final("The deploy script crashed; I checked the logs "
                                 "and found the missing config. Retry?")),
    ], "source": "mentor"}
    monkeypatch.setattr(builder, "DEFAULT_MENTOR", tmp_path / "episodes.jsonl")
    _write_messages_file(builder.DEFAULT_MENTOR, [failure])
    # isolate every other source to tmp (nonexistent) files
    for attr in ("DEFAULT_TOOL_SFT", "DEFAULT_MENTOR_LAB"):
        monkeypatch.setattr(builder, attr, tmp_path / "missing.jsonl")
    monkeypatch.setattr(builder, "DEFAULT_CONVOS", tmp_path / "convos.jsonl")
    monkeypatch.setattr(builder, "DEFAULT_ARABIC_SEED", tmp_path / "ar.jsonl")
    monkeypatch.setattr(builder, "DEFAULT_EXAM", tmp_path / "exam.jsonl")

    out_path = tmp_path / "sft_v2.jsonl"
    report = builder.build(out_path)

    written = [json.loads(line) for line in
               out_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # the x3 upweight survived dedup
    upweight_sources = [r["source"] for r in written
                        if r["source"].startswith("mentor-failure#")]
    assert upweight_sources == ["mentor-failure#1", "mentor-failure#2",
                                "mentor-failure#3"]
    assert report["sources"]["mentor"]["with_failures"] == 1

    # ... while the real duplicate was still deduplicated
    final_answers = [json.loads(r["messages"][-1]["content"])["content"]
                     for r in written if r["source"] == "sft_mix"]
    assert final_answers.count("4") == 1
    assert report["excluded"].get("duplicates") == 1


# ---------------------------------------------------------------------------
# sanitizer: foreign tools, malformed JSON, nameless finals
# ---------------------------------------------------------------------------

def test_sanitizer_drops_foreign_tool_turn_and_orphaned_result() -> None:
    stats: dict = {}
    messages = [
        _msg("user", "read the config file"),
        _msg("assistant", '{"tool": "Read", "arguments": {"file_path": "config.py"}}'),
        _msg("user", "Tool result: {\"ok\": true, \"result\": {\"content\": \"x=1\"}}"),
        _msg("assistant", _final("The config sets x=1.")),
    ]

    cleaned = builder._sanitize_tool_messages(messages, stats)

    # foreign turn AND its orphaned user-result turn are gone
    assert [m["content"] for m in cleaned] == [
        "read the config file", _final("The config sets x=1.")]
    assert stats["foreign_tool_turns_dropped"] == 1


def test_sanitizer_repairs_near_miss_tool_json() -> None:
    stats: dict = {}
    broken = '{"tool": "read_file", "arguments": {"path": "app.py"}}}'
    messages = [_msg("user", "read app.py"), _msg("assistant", broken)]

    cleaned = builder._sanitize_tool_messages(messages, stats)

    repaired = json.loads(cleaned[1]["content"])
    assert repaired == {"tool": "read_file", "arguments": {"path": "app.py"}}
    assert stats["tool_json_repaired"] == 1


def test_sanitizer_rewrites_nameless_content_reply_to_final() -> None:
    stats: dict = {}
    messages = [
        _msg("user", "hello"),
        _msg("assistant", '{"content": "Hello there!"}'),
    ]

    cleaned = builder._sanitize_tool_messages(messages, stats)

    reply = json.loads(cleaned[1]["content"])
    assert reply == {"tool": "final", "content": "Hello there!"}
    assert stats["nameless_final_rewritten"] == 1


def test_sanitizer_drops_empty_object_reply_and_its_result_turn() -> None:
    stats: dict = {}
    messages = [
        _msg("user", "do the thing"),
        _msg("assistant", "{}"),
        _msg("user", "Tool result: nothing happened"),
        _msg("assistant", _final("I could not do the thing.")),
    ]

    cleaned = builder._sanitize_tool_messages(messages, stats)

    assert [m["content"] for m in cleaned] == [
        "do the thing", _final("I could not do the thing.")]
    assert stats["shapeless_tool_turns_dropped"] == 1


def test_sanitizer_leaves_plain_text_and_valid_protocol_turns_alone() -> None:
    stats: dict = {}
    plain = "Just a normal sentence with {braces} but no JSON."
    valid_call = _tool_call("read_file", {"path": "a.py"})
    messages = [
        _msg("user", "hi"),
        _msg("assistant", plain),
        _msg("assistant", valid_call),
    ]

    cleaned = builder._sanitize_tool_messages(messages, stats)

    assert cleaned == messages
    assert stats == {}


# ---------------------------------------------------------------------------
# conversation logs go through the same sanitizer
# ---------------------------------------------------------------------------

def test_conversation_log_rewrites_nameless_finals(tmp_path: Path) -> None:
    convos = tmp_path / "aali_conversations.jsonl"
    convos.write_text(
        json.dumps({"role": "user", "text": "hi"}) + "\n"
        + json.dumps({"role": "assistant", "text": '{"content": "Hello!"}'}) + "\n",
        encoding="utf-8")

    records, stats = builder.load_conversations(convos)

    assert records and stats["nameless_final_rewritten"] == 1
    reply = json.loads(records[0]["messages"][2]["content"])
    assert reply["tool"] == "final"
    assert reply["content"] == "Hello!"


# ---------------------------------------------------------------------------
# token budgets (2026-09-12 night): Arabic rows must fit the trainer window
# ---------------------------------------------------------------------------

def _ar_row(source: str = "mentor-failure#1") -> dict:
    """~2000-char Arabic prompt row: passes the old 2200-char cap, busts the
    token one (Arabic ~1.5 chars/token -> ~1350 estimated prompt tokens)."""
    arabic_text = "هذا نص عربي طويل جداً " * 90
    return {
        "messages": [
            _msg("system", "أنت آلي. تجيب بلغة المستخدم."),
            _msg("user", arabic_text),
            _msg("user", "كم المجموع؟"),
            _msg("assistant", _final("المجموع صفر.")),
        ],
        "source": source,
    }


def _ar_big_answer_row(source: str = "arabic-seed") -> dict:
    """Row whose ANSWER alone busts ANSWER_TOKEN_BUDGET - untrimmable (the
    imitation target must never be truncated), so it must be dropped."""
    return {
        "messages": [
            _msg("system", "sys"),
            _msg("user", "hi"),
            _msg("assistant", _final("رد طويل جداً " * 60)),
        ],
        "source": source,
    }


def test_token_report_counts_arabic_heavily() -> None:
    row = _ar_row()
    report = builder.token_report(row)
    total_chars = sum(len(m["content"]) for m in row["messages"])
    # the whole point: Arabic must cost far more than the naive len/4 guess
    assert report["total"] > total_chars / 4
    assert report["prompt"] == report["total"] - report["answer"]


def test_row_fits_rejects_arabic_row_that_swamps_the_window() -> None:
    assert not builder.row_fits(_ar_row())
    short = {"messages": [_msg("user", "hi"), _msg("assistant", _final("hey"))],
             "source": "mentor"}
    assert builder.row_fits(short)


def test_trim_rescues_long_arabic_row_under_token_budget() -> None:
    row = _ar_row()
    trimmed = builder.trim_to_budget(row)
    assert trimmed is not None
    assert builder.row_fits(trimmed)
    # the answer survives WHOLE (never a truncated imitation target)
    assert trimmed["messages"][-1]["content"] == _final("المجموع صفر.")
    # the task phrase survives too - truncated, not dropped
    assert any("هذا نص عربي" in m["content"] for m in trimmed["messages"])


def test_trim_drops_rows_whose_answer_cannot_survive_whole() -> None:
    assert builder.trim_to_budget(_ar_big_answer_row()) is None


def test_write_gate_trims_or_drops_rows_the_trainer_would_reject(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression that killed 3 train attempts must never reach disk:
    rescuable rows are trimmed; unrescuable ones are dropped AND named."""
    good = {"messages": [_msg("user", "hello"), _msg("assistant", _final("hi!"))],
            "source": "sft_mix"}
    monkeypatch.setattr(builder, "DEFAULT_SFT_MIX", tmp_path / "sft_mix.jsonl")
    _write_messages_file(builder.DEFAULT_SFT_MIX,
                         [good, _ar_row(), _ar_big_answer_row()])
    for attr in ("DEFAULT_TOOL_SFT", "DEFAULT_MENTOR", "DEFAULT_MENTOR_LAB",
                 "DEFAULT_CONVOS", "DEFAULT_ARABIC_SEED", "DEFAULT_EXAM"):
        monkeypatch.setattr(builder, attr, tmp_path / "missing.jsonl")
    monkeypatch.setattr(builder, "generated_episodes", list)

    out_path = tmp_path / "sft_v2.jsonl"
    report = builder.build(out_path)

    written = [json.loads(line) for line in
               out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # good row + trimmed Arabic row; the untrimmable one is gone
    assert len(written) == 2
    assert all(builder.row_fits(r) for r in written)
    drops = report["excluded"].get("dropped_at_write", {})
    assert drops.get("count") == 1
    # load_sft_mix rewrites sources to 'sft_mix' - the drop is named by the
    # source the record carried when it reached the write gate
    assert "sft_mix" in drops.get("sources", [])


def test_conversation_chunks_ending_on_user_turn_are_dropped(tmp_path: Path) -> None:
    """Regression (2026-09-12 night, defect #2): chunks whose assistant answer
    was sanitized away (or that end user-final) must not reach the dataset -
    soup rejects rows with no causal-loss target."""
    convos = tmp_path / "aali_conversations.jsonl"
    # 8 turns: user-assistant pairs for 4 exchanges; the LAST assistant turn
    # is a disease the sanitizer drops -> the chunk ends on a user turn.
    convos.write_text(
        "\n".join(
            json.dumps({"role": role, "text": text}, ensure_ascii=False)
            for role, text in [
                ("user", "salam"), ("assistant", "ahlan!"),
                ("user", "shu akhbarak"), ("assistant", "tamam"),
                ("user", "open whatsapp link"), ("assistant", "{}"),
                ("user", "hello? you there?"),
            ]
        ) + "\n",
        encoding="utf-8")

    records, stats = builder.load_conversations(convos)

    # chunk (7 turns) ends user-final after the "{}" sanitization -> dropped
    assert records == []
    assert stats["chunks_without_assistant_target"] == 1


def test_conversation_chunk_sanitization_keeps_valid_pairs(tmp_path: Path) -> None:
    convos = tmp_path / "aali_conversations.jsonl"
    convos.write_text(
        json.dumps({"role": "user", "text": "hi"}) + "\n"
        + json.dumps({"role": "assistant", "text": '{"content": "Hello!"}'}) + "\n",
        encoding="utf-8")

    records, stats = builder.load_conversations(convos)

    # nameless final rewritten, chunk kept, ends with the rewritten assistant
    assert len(records) == 1
    assert records[0]["messages"][-1]["role"] == "assistant"
    assert "chunks_without_assistant_target" not in stats


def test_token_overflow_rows_names_the_failure(tmp_path: Path) -> None:
    """The pipeline tripwire: the same rejection the trainer applies, but in
    one second and with the offending rows named."""
    good = {"messages": [_msg("user", "hi"), _msg("assistant", _final("hey"))],
            "source": "sft_mix"}
    path = tmp_path / "sft_v2.jsonl"
    _write_messages_file(path, [good, _ar_row()])

    overflow = builder.token_overflow_rows(path, max_length=1024)

    assert [r["row"] for r in overflow] == [1]
    assert overflow[0]["source"] == "mentor-failure#1"
    assert overflow[0]["prompt_tokens_est"] >= 1024 - 18
    # a user-only row is structurally unusable even though it is tiny
    user_only = {"messages": [_msg("system", "s"), _msg("user", "hi")],
                 "source": "conversation-log"}
    _write_messages_file(path, [good, _ar_row(), user_only])
    overflow2 = builder.token_overflow_rows(path, max_length=1024)
    assert overflow2[-1]["reason"].startswith("no assistant target")
    # canary on the deployed dataset (skipped silently where it doesn't exist,
    # e.g. CI): the shipped sft_v2 must have zero trainer-rejected rows
    live = builder.DEFAULT_OUT
    if live.exists():
        assert builder.token_overflow_rows(
            live, max_length=builder.TRAIN_MAX_LENGTH) == []
