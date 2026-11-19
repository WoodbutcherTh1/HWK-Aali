"""Small causal Transformer implemented from scratch with PyTorch.

Uses rotary position embeddings (RoPE) instead of learned position vectors so
the context window is a training-time choice rather than a hard architectural
limit: the same weights can later be fine-tuned (or, with modest degradation,
sampled) at longer sequence lengths than pretraining used.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn
from torch.nn import functional as F
from torch.utils.checkpoint import checkpoint as _grad_checkpoint

from .tokenizer import VOCAB_SIZE


@dataclass
class ModelConfig:
    vocab_size: int = VOCAB_SIZE
    context_size: int = 256  # training window; RoPE allows later extension
    d_model: int = 256
    n_heads: int = 8
    n_layers: int = 6
    dropout: float = 0.1
    rope_base: float = 10000.0


# Cross-entropy chunk size (tokens per chunk).
#
# 2026-10-27: the output layer, not attention, is the memory wall at long
# context. A single F.cross_entropy over [tokens x 32000 vocab] materialises
# fp32 logits plus softmax buffers, which is ~6 GB on its own at 16384 tokens.
# Measured on the 8 GB RTX 3070 (one real forward+backward, this exact model):
#   ctx 8192  stock 3.32 GB | chunked 2.72 GB
#   ctx 16384 stock 6.43 GB | chunked 5.82 GB
# The stock path left so little headroom that every stage-6 (16k) attempt died
# on the FIRST backward with `RuntimeError: CUDA driver error: device not ready`
# - a Windows/WDDM video-memory reset rather than the catchable
# torch.OutOfMemoryError the training loop retries. Chunking is numerically
# identical (cross-entropy is a mean over positions) and was verified to give
# the same loss to four decimals on both paths.
CE_CHUNK_TOKENS = 2048


def _chunked_cross_entropy(
    logits: Tensor,
    labels: Tensor,
    vocab_size: int,
    ignore_index: int = -100,
    chunk: int | None = None,
) -> Tensor:
    """F.cross_entropy over the flattened batch, computed in token chunks.

    Exactly equal to the single-call mean (sum of per-token losses divided by
    the number of non-ignored targets), but the transient fp32 softmax buffers
    are bounded by `chunk x vocab_size` instead of `tokens x vocab_size`.
    """
    # Resolved at CALL time (not as a default argument) so the module constant
    # - and any test or operator that raises it - really governs the chunking.
    if chunk is None:
        chunk = CE_CHUNK_TOKENS
    flat_logits = logits.reshape(-1, vocab_size)
    flat_labels = labels.reshape(-1)
    total = flat_labels.numel()
    if total <= chunk:
        return F.cross_entropy(flat_logits, flat_labels, ignore_index=ignore_index)
    counted = int((flat_labels != ignore_index).sum())
    if counted == 0:
        # Every target masked: matches F.cross_entropy's mean-of-nothing (nan)
        # rather than inventing a zero to train on.
        return F.cross_entropy(flat_logits, flat_labels, ignore_index=ignore_index)
    accumulated: Tensor | None = None
    for start in range(0, total, chunk):
        stop = min(start + chunk, total)
        piece = F.cross_entropy(
            flat_logits[start:stop],
            flat_labels[start:stop],
            ignore_index=ignore_index,
            reduction="sum",
        )
        accumulated = piece if accumulated is None else accumulated + piece
    assert accumulated is not None  # total > chunk >= 1 guarantees one pass
    return accumulated / counted


def _rotary_half(x: Tensor) -> Tensor:
    """Rotate the two halves of the last dimension (RoPE trick)."""
    x1, x2 = x.chunk(2, dim=-1)
    return torch.cat((-x2, x1), dim=-1)


def _rotary_embeddings(
    sequence_length: int,
    head_dim: int,
    base: float,
    device: torch.device,
    dtype: torch.dtype,
) -> tuple[Tensor, Tensor]:
    """Return (cos, sin) of shape (sequence_length, head_dim)."""
    inverse_frequency = 1.0 / (
        base ** (torch.arange(0, head_dim, 2, device=device, dtype=dtype) / head_dim)
    )
    positions = torch.arange(sequence_length, device=device, dtype=dtype)
    angles = torch.einsum("t,f->tf", positions, inverse_frequency)  # (T, head_dim/2)
    angles = torch.cat((angles, angles), dim=-1)  # (T, head_dim)
    return angles.cos(), angles.sin()


def _apply_rotary(x: Tensor, cos: Tensor, sin: Tensor) -> Tensor:
    """x is (batch, heads, sequence, head_dim); cos/sin are (sequence, head_dim)."""
    cos = cos.unsqueeze(0).unsqueeze(0)
    sin = sin.unsqueeze(0).unsqueeze(0)
    return x * cos + _rotary_half(x) * sin


class CausalBlock(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.head_dim = config.d_model // config.n_heads
        self.ln_1 = nn.LayerNorm(config.d_model)
        self.qkv = nn.Linear(config.d_model, 3 * config.d_model)
        self.attention_out = nn.Linear(config.d_model, config.d_model)
        self.attention_dropout = nn.Dropout(config.dropout)
        self.ln_2 = nn.LayerNorm(config.d_model)
        hidden = config.d_model * 4
        self.mlp = nn.Sequential(
            nn.Linear(config.d_model, hidden),
            nn.GELU(),
            nn.Linear(hidden, config.d_model),
            nn.Dropout(config.dropout),
        )

    def forward(self, x: Tensor) -> Tensor:
        normalised = self.ln_1(x)
        batch_size, sequence_length, d_model = normalised.shape
        n_heads = d_model // self.head_dim
        base = self.rope_base  # type: ignore[attr-defined]
        query, key, value = self.qkv(normalised).chunk(3, dim=-1)
        query = query.view(batch_size, sequence_length, n_heads, self.head_dim).transpose(1, 2)
        key = key.view(batch_size, sequence_length, n_heads, self.head_dim).transpose(1, 2)
        value = value.view(batch_size, sequence_length, n_heads, self.head_dim).transpose(1, 2)
        cos, sin = _rotary_embeddings(
            sequence_length, self.head_dim, base, x.device, x.dtype
        )
        query = _apply_rotary(query, cos, sin)
        key = _apply_rotary(key, cos, sin)
        attended = F.scaled_dot_product_attention(
            query,
            key,
            value,
            dropout_p=self.attention_dropout.p if self.training else 0.0,
            is_causal=True,
        )
        attended = attended.transpose(1, 2).contiguous().view(batch_size, sequence_length, -1)
        attended = self.attention_dropout(self.attention_out(attended))
        x = x + attended
        return x + self.mlp(self.ln_2(x))


class TinyCausalLM(nn.Module):
    def __init__(self, config: ModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or ModelConfig()
        self.token_embedding = nn.Embedding(self.config.vocab_size, self.config.d_model)
        # PyTorch's default Embedding init is N(0,1); because the LM head is
        # tied to the token embedding, that makes init logits ~50x too large
        # (a confidently wrong random model). Use the GPT-2 convention.
        nn.init.normal_(self.token_embedding.weight, mean=0.0, std=0.02)
        self.blocks = nn.ModuleList(
            [CausalBlock(self.config) for _ in range(self.config.n_layers)]
        )
        for block in self.blocks:
            block.rope_base = self.config.rope_base  # type: ignore[attr-defined]
        self.final_norm = nn.LayerNorm(self.config.d_model)
        self.lm_head = nn.Linear(self.config.d_model, self.config.vocab_size, bias=False)
        self.lm_head.weight = self.token_embedding.weight
        # Trades compute for memory: recomputes each block's activations on
        # the backward pass instead of keeping them all resident. Roughly
        # halves peak activation memory (grows ~sqrt(n_layers) instead of
        # ~n_layers), which is what makes a longer --context fit on a small
        # GPU. Off by default so existing runs are unaffected; enable with
        # --grad-checkpoint in train_scratch.py.
        self.gradient_checkpointing = False

    def forward(self, input_ids: Tensor, labels: Tensor | None = None) -> Tensor | tuple[Tensor, Tensor]:
        _, sequence_length = input_ids.shape
        if sequence_length > self.config.context_size:
            raise ValueError(
                f"input sequence ({sequence_length}) is longer than context_size "
                f"({self.config.context_size}); fine-tune or train at a larger context first"
            )
        x = self.token_embedding(input_ids)
        for block in self.blocks:
            if self.gradient_checkpointing and self.training:
                x = _grad_checkpoint(block, x, use_reentrant=False)
            else:
                x = block(x)
        logits = self.lm_head(self.final_norm(x))
        if labels is None:
            return logits
        # The dataset provides the shift (input tokens [0..T-1], targets
        # [1..T]), so loss is computed over full logits/labels of the same
        # length.
        loss = _chunked_cross_entropy(logits, labels, self.config.vocab_size)
        return logits, loss


def save_checkpoint(
    model: TinyCausalLM,
    destination: str | Path,
    *,
    step: int = 0,
    tokenizer: Any | None = None,
) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "config": asdict(model.config),
        "model": model.state_dict(),
        "step": step,
    }
    if tokenizer is not None:
        payload["tokenizer"] = {
            "type": "bpe" if type(tokenizer).__name__ == "BpeTokenizer" else "byte",
            "model_path": getattr(tokenizer, "model_path", None),
            "vocab_size": tokenizer.vocab_size,
            "pad_id": tokenizer.pad_id,
            "bos_id": tokenizer.bos_id,
            "eos_id": tokenizer.eos_id,
        }
    torch.save(payload, path)
    path.with_name("config.json").write_text(
        json.dumps(payload["config"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_checkpoint(
    checkpoint: str | Path,
    *,
    device: str | torch.device = "cpu",
) -> tuple[TinyCausalLM, dict[str, Any]]:
    payload = torch.load(checkpoint, map_location=device, weights_only=False)
    config = ModelConfig(**payload["config"])
    model = TinyCausalLM(config)
    model.load_state_dict(payload["model"])
    model.to(device)
    model.eval()
    return model, payload
