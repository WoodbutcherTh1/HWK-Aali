"""SFT dataset contract: next-token shift, completion-only loss masking,
prompt/completion structure, and the serving-shape prompt.

2026-09-14 night lessons (Phase C), in the order they bit:
1. Identity collapse: SftDataset.batches built targets == inputs (no
   next-token shift) while the model computes loss over full-length logits
   and ignores only -100 - so the SFT silently trained a perfect token-copy
   task (loss 0.0009 on UNSEEN eval rows) and the served brain degenerated
   to an infinite ':' attractor. The pretrain path (_blocks_to_xy) always
   shifted; this pins SFT to the same contract.
2. Empty-reply collapse: the row put the ANSWER before the instruction and
   ended on the bare anchor ("...Assistant:" + EOS), so the model learned
   "anchor -> EOS" and served empty replies. The row must be PROMPT (System,
   tools, turns, instruction, anchor) + " " + COMPLETION (the final assistant
   answer) with the tokenizer EOS AFTER the completion.
3. Ordering: serve time builds System -> tools -> turns -> instruction; a
   messages-first tool list sat out of position.
4. Diluted gradient (run 3): loss over the WHOLE row let the constant
   boilerplate (System line, tool list, instruction, role lines) dominate
   the gradient - the model memorized format while answers stayed weak
   (looping, unk leaks). Standard SFT masks the prompt: targets for prompt
   positions are -100 (the model's ignore_index); only the completion
   tokens (+ the row's EOS) carry gradient.
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import train_scratch  # noqa: E402
from train_scratch import (  # noqa: E402
    SftDataset,
    _sft_completion_text,
    _sft_loss_mask,
    _sft_prompt_text,
    _sft_record_text,
)


class _ToyTokenizer:
    """Char-level stand-in with the REAL tokenizer's special-token contract:
    encode(add_special_tokens=True) wraps text as [BOS] ... [EOS] - the
    masking math in SftDataset depends on that trailing EOS."""

    pad_id = 0
    bos_id = 1
    eos_id = 2

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {"<pad>": 0, "<bos>": 1, "<eos>": 2}

    def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
        ids: list[int] = []
        for char in text:
            if char not in self._vocab:
                self._vocab[char] = len(self._vocab)
            ids.append(self._vocab[char])
        if add_special_tokens:
            return [self.bos_id, *ids, self.eos_id]
        return ids


class _EmptyEncodeTokenizer(_ToyTokenizer):
    """Simulates a row that tokenizes to nothing (degenerate-row drop)."""

    def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
        if text == "x":
            return []
        return super().encode(text, add_special_tokens=add_special_tokens)


def _record(content: str, answer: str = "final answer") -> dict[str, object]:
    return {
        "messages": [
            {"role": "user", "content": content},
            {"role": "assistant", "content": answer},
        ]
    }


def test_sft_batch_targets_are_inputs_shifted_by_one() -> None:
    """The identity-collapse regression: for every row, targets must be the
    inputs shifted left by one (targets[t] = the token AFTER inputs[t]).
    mask_prompt=False isolates the pure shift contract (no -100 anywhere)."""
    tokenizer = _ToyTokenizer()
    dataset = SftDataset([_record("hello world")], tokenizer, context=2048, seed=1,
                         mask_prompt=False)
    seq = dataset.sequences[0]
    assert len(seq) >= 2

    inputs, targets = next(dataset.batches(batch_size=1, pad_id=0))

    assert inputs.shape == targets.shape
    # single-row batch: width is exactly len(seq)-1, no padding anywhere
    assert inputs.shape[1] == len(seq) - 1
    assert inputs[0].tolist() == seq[:-1]
    assert targets[0].tolist() == seq[1:]
    assert (targets[0] != -100).all()


def test_sft_batch_pads_shorter_rows_with_minus_100_targets() -> None:
    """Mixed-length batch (mask_prompt=False): shorter rows keep their
    shifted (input, target) pair in the leading columns and are padded with
    pad_id inputs / -100 (loss-ignored) targets after - never real tokens
    trained as pads."""
    tokenizer = _ToyTokenizer()
    # context must clear the render overhead (System line + tool list +
    # instruction) so the two rows keep DIFFERENT lengths for the padding test
    records = [_record("a"), _record("a" * 80)]
    dataset = SftDataset(records, tokenizer, context=2048, seed=1, mask_prompt=False)
    lens = [len(s) for s in dataset.sequences]
    assert lens[0] < lens[1]  # the two rows really do differ in length
    width = max(lens) - 1

    inputs, targets = next(dataset.batches(batch_size=2, pad_id=0))

    assert inputs.shape == (2, width)
    assert targets.shape == (2, width)
    # batches() SHUFFLES, so batch row order is not dataset order - match each
    # row back to its sequence by content instead of by position.
    for row in range(2):
        matches = [
            seq for seq in dataset.sequences
            if inputs[row, : len(seq) - 1].tolist() == seq[:-1]
        ]
        assert len(matches) == 1  # exactly one sequence fits this row
        seq = matches[0]
        n = len(seq) - 1
        assert targets[row, :n].tolist() == seq[1:]
        # ... and the shorter row is padded with pad_id inputs / -100
        # (loss-ignored) targets after its content - never real tokens.
        if n < width:
            assert (targets[row, n:] == -100).all(), (
                "padding targets must be -100 so the loss ignores them")
            assert (inputs[row, n:] == tokenizer.pad_id).all()


def test_sft_targets_mask_prompt_positions() -> None:
    """The diluted-gradient regression (default mask_prompt=True): with the
    row stored as [BOS] prompt completion [EOS] and mask = 0*prompt + 1*comp,
    shifted targets must be -100 EXACTLY where the mask says ignore, and real
    tokens everywhere the mask marks as completion - including the row's own
    EOS as the final unmasked target (the model must learn to STOP)."""
    tokenizer = _ToyTokenizer()
    dataset = SftDataset([_record("hello world", answer="the ANSWER")],
                         tokenizer, context=2048, seed=1)
    seq = dataset.sequences[0]
    mask = dataset.masks[0]

    inputs, targets = next(dataset.batches(batch_size=1, pad_id=0))

    assert len(mask) == len(seq)
    assert mask[-1] == 1  # EOS stays unmasked: learn to stop after the answer
    first_kept = mask.index(1)
    assert all(m == 0 for m in mask[:first_kept])  # prompt side fully masked
    for t in range(len(seq) - 1):
        if mask[t + 1] == 1:
            assert targets[0, t].item() == seq[t + 1], (
                f"unmasked target position {t} must carry the shifted token")
        else:
            assert targets[0, t].item() == -100, (
                f"prompt target position {t} must be -100 (loss-ignored)")
    # inputs are untouched by masking
    assert inputs[0].tolist() == seq[:-1]


def test_sft_unmasked_targets_are_completion_plus_eos() -> None:
    """The unmasked token count must equal completion tokens + 1 EOS - the
    whole point of masking: gradient flows ONLY into the answer and stop."""
    tokenizer = _ToyTokenizer()
    answer = "the ANSWER text"
    dataset = SftDataset([_record("hello world", answer=answer)],
                         tokenizer, context=2048, seed=1)
    completion_ids = tokenizer.encode(answer, add_special_tokens=False)

    _inputs, targets = next(dataset.batches(batch_size=1, pad_id=0))
    trained = int((targets[0] != -100).sum().item())
    assert trained == len(completion_ids) + 1  # +1: the row's EOS


def test_sft_mask_prompt_false_masks_nothing() -> None:
    """mask_prompt=False restores the legacy whole-row loss (escape hatch):
    no -100 inside the content region (padding aside)."""
    tokenizer = _ToyTokenizer()
    dataset = SftDataset([_record("hello world")], tokenizer, context=2048,
                         seed=1, mask_prompt=False)
    seq = dataset.sequences[0]
    assert all(m == 1 for m in dataset.masks[0])

    _inputs, targets = next(dataset.batches(batch_size=1, pad_id=0))
    assert (targets[0] != -100).all()


def test_sft_masked_mixed_batch_matches_each_rows_mask() -> None:
    """Masking holds per-row in a mixed-length SHUFFLED batch (rows are
    matched back by content because batches() shuffles)."""
    tokenizer = _ToyTokenizer()
    records = [_record("a", answer="short"), _record("b" * 80, answer="much longer answer")]
    dataset = SftDataset(records, tokenizer, context=2048, seed=1)
    width = max(len(s) for s in dataset.sequences) - 1

    inputs, targets = next(dataset.batches(batch_size=2, pad_id=0))

    for row in range(2):
        matches = [
            (seq, mask) for seq, mask in zip(dataset.sequences, dataset.masks)
            if inputs[row, : len(seq) - 1].tolist() == seq[:-1]
        ]
        assert len(matches) == 1
        seq, mask = matches[0]
        for t in range(len(seq) - 1):
            expected = seq[t + 1] if mask[t + 1] == 1 else -100
            assert targets[row, t].item() == expected
        # padding stays -100 regardless of masking
        if len(seq) - 1 < width:
            assert (targets[row, len(seq) - 1:] == -100).all()


def test_sft_loss_mask_contract() -> None:
    """_sft_loss_mask returns (row, completion) where the completion is the
    row's SUFFIX - the property the token-boundary mask math relies on."""
    record = _record("hi", answer="Here is my answer.")
    row, completion = _sft_loss_mask(record)
    assert row == _sft_record_text(record)
    assert completion == _sft_completion_text(record)
    assert row.endswith(completion)


def test_sft_drops_degenerate_rows(monkeypatch) -> None:
    """A row that tokenizes to fewer than 2 ids has no (input, target) pair;
    it must be dropped at build time, not crash the batch later. The collapse
    is simulated by patching the row renderer (the real serving-shape row is
    never degenerate)."""
    tokenizer = _EmptyEncodeTokenizer()

    def fake_row(record: dict[str, object]) -> tuple[str, str]:
        messages = record.get("messages")
        first = messages[0].get("content") if isinstance(messages, list) else ""
        return ("x", "x") if first == "a" else ("longer content here so it survives", "ok")

    monkeypatch.setattr(train_scratch, "_sft_loss_mask", fake_row)
    dataset = SftDataset(
        [_record("a"), _record("longer content here so it survives")],
        tokenizer, context=256, seed=1,
    )
    assert len(dataset) == 1  # the degenerate row was dropped
    inputs, _targets = next(dataset.batches(batch_size=1, pad_id=0))
    assert inputs.shape[1] >= 1


def test_sft_every_row_keeps_an_unmasked_target() -> None:
    """Whatever the masking decides, every kept row must retain at least one
    trained (unmasked) target - a fully-masked row would contribute a zero
    loss and silently dilute the batch."""
    tokenizer = _ToyTokenizer()
    dataset = SftDataset(
        [_record("a"), _record("longer content", answer="ok")],
        tokenizer, context=2048, seed=1,
    )
    assert len(dataset) == 2
    assert all(any(m == 1 for m in mask) for mask in dataset.masks)


def test_sft_record_text_matches_serving_shape() -> None:
    """The row must carry the SAME instruction + Assistant: anchor the
    runtime appends at serve time (agent_loop._scratch_prompt), so the
    served prompt is in-distribution for the trained model."""
    text = _sft_record_text(_record("hi"))
    assert "Reply with either a natural-language answer or exactly one JSON object." in text
    assert '{"tool":"tool_name","arguments":{...}}' in text
    assert '{"tool":"final","content":"..."}' in text
    assert text.rstrip().endswith("Assistant: final answer")


def test_sft_completion_follows_the_anchor() -> None:
    """The empty-reply regression: the ANSWER must sit AFTER the Assistant:
    anchor (the model generates it at serve time), with no instruction text
    after it - only then does the tokenizer EOS mark 'answer finished'."""
    text = _sft_record_text(_record("hi", answer="Here is my answer."))
    anchor = text.rindex("Assistant:")
    tail = text[anchor + len("Assistant:"):]
    assert tail.startswith(" Here is my answer.")
    assert "Reply with either" not in tail  # instruction stayed in the prompt


def test_sft_prompt_excludes_the_final_answer() -> None:
    """The final assistant turn is the COMPLETION, never prompt context -
    otherwise the answer is memorized as input and never generated."""
    prompt = _sft_prompt_text(_record("hi", answer="SECRETANSWER"))
    assert "SECRETANSWER" not in prompt
    assert _sft_completion_text(_record("hi", answer="SECRETANSWER")) == "SECRETANSWER"


def test_sft_multi_turn_keeps_leading_turns_and_final_answer() -> None:
    """Multi-turn episodes: earlier turns stay in the prompt (history the
    model conditions on), only the LAST assistant turn is the completion -
    mirroring how history accumulates at serving."""
    record = {
        "messages": [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second reply"},
        ]
    }
    prompt = _sft_prompt_text(record)
    assert "User: first question" in prompt
    assert "Assistant: first reply" in prompt
    assert "User: second question" in prompt
    assert "second reply" not in prompt
    assert _sft_completion_text(record) == "second reply"
    row = _sft_record_text(record)
    assert row.index("Assistant: first reply") < row.rindex("Assistant: second reply")


def test_sft_record_requires_an_assistant_turn() -> None:
    """A record with no assistant turn has nothing to complete - it must fail
    loudly instead of silently training an answerless row."""
    try:
        _sft_completion_text({"messages": [{"role": "user", "content": "hi"}]})
    except ValueError:
        return
    raise AssertionError("expected ValueError for a record without an assistant turn")


def test_sft_record_text_keeps_role_lines() -> None:
    """Role lines keep the serving format's 'Role: content' shape - the user
    turn as a prompt line, the answer generated after the anchor."""
    text = _sft_record_text(_record("hi"))
    assert "\nUser: hi\n" in text
    assert text.rstrip().endswith("Assistant: final answer")


def test_sft_record_text_ordering_matches_serving_transcript() -> None:
    """Serve time (_local_model_loop) builds: System -> memory -> Available
    tools -> turns -> instruction. Training rows must keep the same ORDER
    (System line, THEN tools, THEN turns) - a messages-first tool list was
    the second format gap found the same night."""
    text = _sft_record_text(_record("hi"))
    system_pos = text.index("System: You are Aali.")
    tools_pos = text.index("Available tools:")
    user_pos = text.index("User: hi")
    assistant_anchor = text.rindex("Assistant:")
    assert system_pos < tools_pos < user_pos < assistant_anchor
