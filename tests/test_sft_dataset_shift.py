"""SFT dataset contract: the next-token shift + the serving-shape prompt.

2026-09-14 night lesson (Phase C identity collapse): SftDataset.batches built
targets == inputs (no next-token shift) while the model computes loss over
full-length logits and ignores only -100 — so the SFT silently trained a
perfect token-copy task (loss 0.0009 on UNSEEN eval rows) and the served
brain degenerated to an infinite ':' attractor. The pretrain path
(_blocks_to_xy) always shifted; this pins SFT to the same contract, and pins
the record text to the runtime's exact serving shape (_scratch_prompt), so
the format gap cannot silently reopen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import train_scratch  # noqa: E402
from train_scratch import SftDataset, _message_text, _sft_record_text  # noqa: E402


class _ToyTokenizer:
    """Whitespace/char-level stand-in with the pieces SftDataset touches."""

    pad_id = 0

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {"<pad>": 0}

    def encode(self, text: str) -> list[int]:
        ids: list[int] = []
        for char in text:
            if char not in self._vocab:
                self._vocab[char] = len(self._vocab)
            ids.append(self._vocab[char])
        return ids


def _record(content: str) -> dict[str, object]:
    return {
        "messages": [
            {"role": "user", "content": content},
            {"role": "assistant", "content": "final answer"},
        ]
    }


def test_sft_batch_targets_are_inputs_shifted_by_one() -> None:
    """The identity-collapse regression: for every row, targets must be the
    inputs shifted left by one (targets[t] = the token AFTER inputs[t]).
    Ground truth = the dataset's own stored token sequences."""
    tokenizer = _ToyTokenizer()
    dataset = SftDataset([_record("hello world")], tokenizer, context=256, seed=1)
    seq = dataset.sequences[0]
    assert len(seq) >= 2

    inputs, targets = next(dataset.batches(batch_size=1, pad_id=0))

    assert inputs.shape == targets.shape
    # single-row batch: width is exactly len(seq)-1, no padding anywhere
    assert inputs.shape[1] == len(seq) - 1
    assert inputs[0].tolist() == seq[:-1]
    assert targets[0].tolist() == seq[1:]


def test_sft_batch_pads_shorter_rows_with_minus_100_targets() -> None:
    """Mixed-length batch: shorter rows keep their shifted (input, target)
    pair in the leading columns and are padded with pad_id inputs / -100
    (loss-ignored) targets after - never real tokens trained as pads."""
    tokenizer = _ToyTokenizer()
    # context must clear the render overhead (System line + tool list +
    # instruction) so the two rows keep DIFFERENT lengths for the padding test
    records = [_record("a"), _record("a" * 80)]
    dataset = SftDataset(records, tokenizer, context=2048, seed=1)
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


def test_sft_never_yields_single_token_sequences(monkeypatch) -> None:
    """A 1-token sequence has no (input, target) pair; it was silently usable
    before the shift fix and would produce a 0-width row. The render now adds
    fixed overhead, so the collapse is simulated by patching the renderer."""
    tokenizer = _ToyTokenizer()

    def fake_render(record: dict[str, object]) -> str:
        messages = record.get("messages")
        first = messages[0].get("content") if isinstance(messages, list) else ""
        return "x" if first == "a" else "longer content here so it survives"

    monkeypatch.setattr(train_scratch, "_sft_record_text", fake_render)
    dataset = SftDataset(
        [_record("a"), _record("longer content here so it survives")],
        tokenizer, context=256, seed=1,
    )
    assert len(dataset) == 1  # the 1-token row was dropped at build time
    inputs, _targets = next(dataset.batches(batch_size=1, pad_id=0))
    assert inputs.shape[1] >= 1


def test_sft_record_text_matches_serving_shape() -> None:
    """The record text must carry the SAME instruction + Assistant: anchor
    the runtime appends at serve time (agent_loop._scratch_prompt), so the
    served prompt is in-distribution for the trained model."""
    text = _sft_record_text(_record("hi"))
    assert "Reply with either a natural-language answer or exactly one JSON object." in text
    assert '{"tool":"tool_name","arguments":{...}}' in text
    assert '{"tool":"final","content":"..."}' in text
    assert text.rstrip().endswith("Assistant:")


def test_sft_record_text_keeps_role_lines() -> None:
    """Role lines come from _message_text and must stay the serving format's
    'Role: content' shape."""
    text = _sft_record_text(_record("hi"))
    assert "User: hi" in text
    assert "Assistant: final answer" in text
    assert _message_text(_record("hi")) in text


def test_sft_record_text_ordering_matches_serving_transcript() -> None:
    """Serve time (_local_model_loop) builds: System -> memory -> Available
    tools -> turns -> instruction. Training rows must keep the same ORDER
    (System line, THEN tools, THEN turns) - a messages-first tool list was
    the second format gap found the same night."""
    text = _sft_record_text(_record("hi"))
    system_pos = text.index("System: You are Aali.")
    tools_pos = text.index("Available tools:")
    user_pos = text.index("User: hi")
    assistant_anchor = text.index("\nAssistant:")
    assert system_pos < tools_pos < user_pos < assistant_anchor
