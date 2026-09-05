"""SentencePiece BPE tokenizer trained from scratch on our own corpus.

This is still fully "from scratch": the vocabulary is learned from the
downloaded Arabic/English/code text; nothing is downloaded as a pretrained
vocabulary. It replaces the byte fallback for serious training while keeping
the same encode/decode interface as ByteTokenizer.

Layout:
  <pad>=0  <bos>=1  <eos>=2  <unk>=3
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import sentencepiece as spm

PAD_ID = 0
BOS_ID = 1
EOS_ID = 2
UNK_ID = 3
DEFAULT_VOCAB_SIZE = 32000
MODEL_PREFIX = "hwk_spm"


@dataclass
class BpeTokenizer:
    """BPE tokenizer backed by a sentencepiece model file."""

    model_path: str
    pad_id: int = PAD_ID
    bos_id: int = BOS_ID
    eos_id: int = EOS_ID
    unk_id: int = UNK_ID
    vocab_size: int = DEFAULT_VOCAB_SIZE

    def __post_init__(self) -> None:
        self._processor = spm.SentencePieceProcessor(model_file=self.model_path)
        # The authoritative vocab size comes from the trained model file.
        self.vocab_size = self._processor.get_piece_size()

    def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        tokens = self._processor.encode(text, out_type=int)
        if add_special_tokens:
            return [self.bos_id, *tokens, self.eos_id]
        return tokens

    def decode(self, tokens: list[int]) -> str:
        return self._processor.decode(tokens)

    def piece_to_id(self, piece: str) -> int:
        return self._processor.piece_to_id(piece)


def train_bpe(
    inputs: list[str | Path],
    output_dir: str | Path,
    *,
    vocab_size: int = DEFAULT_VOCAB_SIZE,
    model_type: str = "bpe",
    character_coverage: float = 0.9995,
    sentencepiece_size: int = 4_000_000,  # sampled sentences used for training
) -> BpeTokenizer:
    """Train a BPE model from text files (plain text or JSONL with text/content).

    input_sentence_size limits how many sentences sentencepiece samples, which
    keeps training fast while still covering the corpus vocabulary well.
    """
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    prefix = str(destination / MODEL_PREFIX)

    spm.SentencePieceTrainer.train(
        input=[str(path) for path in inputs],
        model_prefix=prefix,
        model_type=model_type,
        vocab_size=vocab_size,
        character_coverage=character_coverage,
        input_sentence_size=sentencepiece_size,
        shuffle_input_sentence=True,
        bos_id=BOS_ID,
        eos_id=EOS_ID,
        unk_id=UNK_ID,
        pad_id=PAD_ID,
        user_defined_symbols=["<|system|>", "<|user|>", "<|assistant|>", "<|tool|>"],
        pad_piece="<pad>",
    )
    model_path = f"{prefix}.model"
    return BpeTokenizer(model_path, vocab_size=vocab_size)


def load_bpe(model_path: str | Path) -> BpeTokenizer:
    path = Path(model_path)
    if path.is_dir():
        path = path / f"{MODEL_PREFIX}.model"
    return BpeTokenizer(str(path))


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        raise SystemExit("usage: python bpe_tokenizer.py <input1> [input2 ...] <output_dir>")
    inputs, output = sys.argv[1:-1], sys.argv[-1]
    tokenizer = train_bpe(inputs, output)
    print(f"trained BPE vocab={tokenizer.vocab_size} model={tokenizer.model_path}")
    for sample in ["مرحباً بالعالم", "def hello(name): return f'Hello {name}'", "The court held that the contract is valid."]:
        ids = tokenizer.encode(sample)
        print(f"{sample!r} -> {ids} -> {tokenizer.decode(ids)}")