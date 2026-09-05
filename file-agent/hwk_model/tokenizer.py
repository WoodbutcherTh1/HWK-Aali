"""A dependency-free UTF-8 byte tokenizer for Arabic and other languages."""

from __future__ import annotations

from dataclasses import dataclass


PAD_ID = 256
BOS_ID = 257
EOS_ID = 258
VOCAB_SIZE = 259


@dataclass(frozen=True)
class ByteTokenizer:
    """Encode any Unicode text without downloading a vocabulary."""

    pad_id: int = PAD_ID
    bos_id: int = BOS_ID
    eos_id: int = EOS_ID
    vocab_size: int = VOCAB_SIZE

    def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        tokens = list(text.encode("utf-8"))
        if add_special_tokens:
            return [self.bos_id, *tokens, self.eos_id]
        return tokens

    def decode(self, tokens: list[int]) -> str:
        raw = bytes(token for token in tokens if 0 <= token < 256)
        return raw.decode("utf-8", errors="replace")