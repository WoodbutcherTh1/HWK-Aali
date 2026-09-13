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


# ---------------------------------------------------------------------------
# sft_v3 media block + floors gate (2026-09-12 postmortem: media 0/5 because
# the mix had 7 media rows and 6 of 13 authored episodes died as exam leaks)
# ---------------------------------------------------------------------------

def test_media_tool_counts_counts_rows_per_tool() -> None:
    rows = [
        {"messages": [_msg("user", "draw"),
                      _msg("assistant", '{"tool": "generate_image", "arguments": {}}')],
         "source": "a"},
        {"messages": [_msg("user", "clip"),
                      _msg("assistant", '{"tool": "generate_video", "arguments": {}}')],
         "source": "b"},
        {"messages": [_msg("user", "final"),
                      _msg("assistant", '{"tool": "final", "content": "no tool"}')],
         "source": "c"},
    ]
    counts = builder.media_tool_counts(rows)
    assert counts["generate_image"] == 1
    assert counts["generate_video"] == 1
    assert counts["edit_image"] == 0


def test_media_floor_failures_names_each_shortfall() -> None:
    counts = dict.fromkeys(builder.MEDIA_FLOORS, 99)
    assert builder.media_floor_failures(counts) == []
    counts = {tool: 0 for tool in builder.MEDIA_FLOORS}
    failures = builder.media_floor_failures(counts)
    assert len(failures) == len(builder.MEDIA_FLOORS)
    assert any("generate_image" in f and "floor 12" in f for f in failures)


def test_generated_media_block_meets_every_floor_and_leaks_nothing(
        tmp_path: Path) -> None:
    """The exact failure that gutted the 09-12 mix, now a test: the authored
    media episodes must clear the floors on their own, and NONE of them may
    reuse an exam user-turn (the leak gate killed 6 of the old 13)."""
    rows = builder.generated_episodes()
    failures = builder.media_floor_failures(builder.media_tool_counts(rows))
    assert failures == [], failures
    # leak check against a real exam file containing the v2 offenders
    exam = tmp_path / "exam.jsonl"
    exam.write_text(json.dumps({"id": "img_basic_en", "turns": [
        ["user", "Draw me a picture of a mountain lake at dawn"]]}) + "\n",
        encoding="utf-8")
    hashes = builder.load_exam_prompts(exam)
    for row in rows:
        user_text, _ = builder._record_text(row)
        assert builder._hash_pair(user_text, "") not in hashes, row["source"]


def test_generate_emoji_episodes_teach_the_real_schema_key() -> None:
    rows = builder.generated_episodes()
    emoji_rows = [r for r in rows
                  if '"generate_emoji"' in r["messages"][-1]["content"]]
    assert len(emoji_rows) >= 6  # floor is 4; authored 6 for headroom
    for row in emoji_rows:
        args = json.loads(row["messages"][-1]["content"])["arguments"]
        assert "prompt" in args and "path" in args
        assert "description" not in args  # the v2 bug: wrong arg name


def test_multi_turn_consent_pair_teaches_refusal_then_call() -> None:
    """The exam's follow-up cases grade two-turn consent-then-call behavior;
    the v2 survivors were single-turn only."""
    rows = builder.generated_episodes()
    by_source = {r["source"]: r for r in rows}
    row = by_source["video-consent-v3-wedding-en"]
    roles = [m["role"] for m in row["messages"]]
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    refusal = json.loads(row["messages"][2]["content"])
    assert refusal["tool"] == "final"
    call = json.loads(row["messages"][4]["content"])
    assert call["tool"] == "generate_video"
    assert set(call["arguments"]) == {"prompt", "path"}


def test_media_error_recovery_episodes_use_honest_finals() -> None:
    rows = builder.generated_episodes()
    by_source = {r["source"]: r for r in rows}
    row = by_source["media-error-v3-path-en"]
    last = row["messages"][-1]
    assert last["role"] == "assistant"
    payload = json.loads(last["content"])
    assert payload["tool"] == "final"
    assert "doesn't exist" in payload["content"]


def test_media_upweight_copies_only_call_emitting_episodes() -> None:
    """v4 draft: copies target the causal-loss target - episodes whose FINAL
    assistant turn is a media CALL. Honest-error-recovery finals (final after
    a failed call mid-conversation) stay at weight 1: the exam grades calls."""
    call_row = {"messages": [_msg("user", "draw a cat"),
                             _msg("assistant", '{"tool": "generate_image", "arguments": {}}')],
                "source": "img-call"}
    recovery_row = {"messages": [
        _msg("user", "draw a cat"),
        _msg("assistant", '{"tool": "generate_image", "arguments": {}}'),
        _msg("user", 'Tool result: {"ok": false, "error": {"message": "x"}}'),
        _msg("assistant", '{"tool": "final", "content": "it failed"}')],
        "source": "img-recovery"}
    copies = builder.media_upweight_copies([call_row, recovery_row], 2)
    sources = [c["source"] for c in copies]
    assert len(copies) == 2  # only the call row, x2
    assert all(s.startswith("media-upweight#") for s in sources)
    assert sources[0].endswith("img-call")


def test_media_upweight_copies_are_byte_identical_and_tagged() -> None:
    row = {"messages": [_msg("user", "clip"),
                       _msg("assistant", '{"tool": "generate_video", "arguments": {}}')],
           "source": "vid-call"}
    copies = builder.media_upweight_copies([row], 3)
    assert len(copies) == 3
    for i, copy in enumerate(copies, start=1):
        assert copy["source"] == f"media-upweight#{i}:vid-call"
        # messages identical to the original (byte-identical content)
        assert copy["messages"] == row["messages"]
    assert builder.media_upweight_copies([row], 0) == []


def test_dedup_exemption_covers_media_upweight_tag() -> None:
    assert builder.is_intentional_upweight("mentor-failure#1")
    assert builder.is_intentional_upweight("media-upweight#2:image-gen-v3-city-en")
    assert not builder.is_intentional_upweight("sft_mix")
    assert not builder.is_intentional_upweight("image-gen-v3-city-en")


def test_media_upweight_disabled_by_default() -> None:
    """v3 must stay the default recipe - the upweight fires only when the
    owner sets AALI_MEDIA_UPWEIGHT."""
    assert builder.MEDIA_UPWEIGHT_DEFAULT == 0


def test_build_report_includes_media_floor_status() -> None:
    """The report must always carry the floor verdict so the pipeline log and
    any human can see it without rerunning the build."""
    assert hasattr(builder, "MEDIA_FLOORS")
    assert builder.MEDIA_FLOORS["generate_image"] >= 12
    assert builder.MEDIA_FLOORS["generate_video"] >= 8
    assert builder.MEDIA_FLOORS["edit_image"] >= 6
    assert builder.MEDIA_FLOORS["read_image"] >= 6
    assert builder.MEDIA_FLOORS["generate_emoji"] >= 4


# ---------------------------------------------------------------------------
# near-miss corrections (2026-09-13: v4 tuned exam invented tool names -
# paint, local_clip, audio_recorder, file, math, registration - and scored
# media 0/5 despite the calls being in the data; contrastive episodes fix it)
# ---------------------------------------------------------------------------

# The REAL file_tools.py registry - any tool name outside this set in an
# assistant turn is a behavior Aali cannot execute.
_REAL_TOOLS = frozenset({
    "list_files", "read_file", "write_file", "append_file",
    "replace_in_file", "make_directory", "move_file", "delete_file",
    "search_files", "run_command", "read_image", "read_document",
    "analyze_video", "make_n8n_workflow", "list_skills", "use_skill",
    "fetch_url", "web_search", "generate_image", "generate_video",
    "edit_image", "edit_video", "machine_ops", "memory", "generate_emoji",
    "final",
})


def test_near_miss_episodes_exist_for_every_exam_invention() -> None:
    """Every wrong name the v4 exam actually emitted must have a correction
    episode family (EN + AR)."""
    episodes = builder.near_miss_correction_episodes()
    # family source tags hyphenate the invented name (video-local-clip);
    # normalize so local_clip matches local-clip.
    by_family = [e["source"].rsplit("-", 1)[0].replace("_", "-")
                 for e in episodes]
    for invented in ("paint", "local_clip", "audio_recorder", "drawing_tool",
                     "video_quality", "file", "math", "registration"):
        assert any(invented.replace("_", "-") in family
                   for family in by_family), invented


def test_near_miss_wrong_names_never_in_assistant_turns() -> None:
    """THE loss-safety contract: soup puts causal loss on EVERY assistant
    turn, so a wrong name there would be TRAINED as the protocol itself.
    Wrong names may appear only in user turns; the trained target is always
    a real registry call (or a plain final)."""
    for episode in builder.near_miss_correction_episodes():
        users = " ".join(m.get("content", "") for m in episode["messages"]
                         if m.get("role") == "user")
        assert any(name in users for name in builder.NEAR_MISS_TOOLS), \
            episode["source"]
        for message in episode["messages"]:
            if message.get("role") != "assistant":
                continue
            text = str(message.get("content", "")).strip()
            match = builder._TOOL_HEAD_RE.match(text)
            if match:
                assert match.group(1) in _REAL_TOOLS, \
                    (episode["source"], match.group(1))


def test_near_miss_call_targets_match_file_tools_schemas() -> None:
    """The corrected calls must use the REAL required arguments (generate_emoji
    takes prompt, edit_image takes path+output+op, memory takes action...)."""
    for episode in builder.near_miss_correction_episodes():
        last = json.loads(episode["messages"][-1]["content"])
        tool, args = last["tool"], last.get("arguments", {})
        if tool == "generate_image" or tool == "generate_video":
            assert set(args) == {"prompt", "path"}, episode["source"]
        elif tool == "generate_emoji":
            assert "prompt" in args and "path" in args, episode["source"]
            assert "description" not in args
        elif tool == "edit_image":
            assert {"path", "output", "op"} <= set(args), episode["source"]
        elif tool == "memory":
            assert args.get("action") in {"save", "recall", "forget",
                                          "summary"}, episode["source"]


def test_near_miss_floors_and_leak_check_helpers() -> None:
    rows = builder.generated_episodes()
    counts = builder.near_miss_tool_counts(rows)
    assert builder.near_miss_floor_failures(counts) == [], counts
    assert builder.near_miss_leak_check(rows) == []
    # a wrong name in an ASSISTANT turn must be flagged
    poisoned = [{"messages": [
        _msg("user", "draw"),
        _msg("assistant", '{"tool": "paint", "arguments": {}}')],
        "source": "bad-row"}]
    assert builder.near_miss_leak_check(poisoned) == ["bad-row"]
    # and a below-floor count must be named
    short = dict.fromkeys(builder.NEAR_MISS_FLOORS, 99)
    short["generate_image"] = 1
    failures = builder.near_miss_floor_failures(short)
    assert any("generate_image" in f and "floor 4" in f for f in failures)


def test_near_miss_episodes_survive_the_full_build(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a build with all other sources empty still writes the
    near-miss corrections, passes both floor gates, and drops nothing."""
    for attr in ("DEFAULT_SFT_MIX", "DEFAULT_TOOL_SFT", "DEFAULT_MENTOR",
                 "DEFAULT_MENTOR_LAB"):
        monkeypatch.setattr(builder, attr, tmp_path / "missing.jsonl")
    monkeypatch.setattr(builder, "DEFAULT_CONVOS", tmp_path / "convos.jsonl")
    monkeypatch.setattr(builder, "DEFAULT_ARABIC_SEED", tmp_path / "ar.jsonl")
    monkeypatch.setattr(builder, "DEFAULT_EXAM", tmp_path / "exam.jsonl")
    out_path = tmp_path / "sft_v2.jsonl"
    report = builder.build(out_path)
    written = [json.loads(line) for line in
               out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sources = [r["source"] for r in written]
    assert sum(1 for s in sources if s.startswith("nearmiss-")) == 40
    assert report["near_miss_floors_ok"] is True
    assert report["near_miss_assistant_leaks"] == []
    assert report["media_floors_ok"] is True
    assert report["final"]["dropped_too_long"] == 0
