"""The from-scratch HWK language model."""

from .bpe_tokenizer import BpeTokenizer, load_bpe, train_bpe
from .tokenizer import ByteTokenizer

try:
    from .model import ModelConfig, TinyCausalLM, load_checkpoint, save_checkpoint
except ImportError:
    # torch isn't installed in this environment (e.g. a tokenizer-only tool
    # like tokenize_corpus.py running somewhere without a GPU/torch setup).
    # Tokenizer classes above still work fine without it.
    ModelConfig = TinyCausalLM = load_checkpoint = save_checkpoint = None

__all__ = [
    "BpeTokenizer",
    "ByteTokenizer",
    "ModelConfig",
    "TinyCausalLM",
    "load_bpe",
    "load_checkpoint",
    "save_checkpoint",
    "train_bpe",
]
