"""Small causal Transformer implemented from scratch with PyTorch."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from .tokenizer import VOCAB_SIZE


@dataclass
class ModelConfig:
    vocab_size: int = VOCAB_SIZE
    context_size: int = 256
    d_model: int = 256
    n_heads: int = 8
    n_layers: int = 6
    dropout: float = 0.1


class CausalBlock(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.ln_1 = nn.LayerNorm(config.d_model)
        self.attention = nn.MultiheadAttention(
            config.d_model,
            config.n_heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.ln_2 = nn.LayerNorm(config.d_model)
        hidden = config.d_model * 4
        self.mlp = nn.Sequential(
            nn.Linear(config.d_model, hidden),
            nn.GELU(),
            nn.Linear(hidden, config.d_model),
            nn.Dropout(config.dropout),
        )

    def forward(self, x: Tensor, mask: Tensor) -> Tensor:
        normalised = self.ln_1(x)
        attended, _ = self.attention(
            normalised,
            normalised,
            normalised,
            attn_mask=mask,
            need_weights=False,
        )
        x = x + attended
        return x + self.mlp(self.ln_2(x))


class TinyCausalLM(nn.Module):
    def __init__(self, config: ModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or ModelConfig()
        self.token_embedding = nn.Embedding(self.config.vocab_size, self.config.d_model)
        self.position_embedding = nn.Embedding(self.config.context_size, self.config.d_model)
        self.blocks = nn.ModuleList(
            [CausalBlock(self.config) for _ in range(self.config.n_layers)]
        )
        self.final_norm = nn.LayerNorm(self.config.d_model)
        self.lm_head = nn.Linear(self.config.d_model, self.config.vocab_size, bias=False)
        self.lm_head.weight = self.token_embedding.weight

    def forward(self, input_ids: Tensor, labels: Tensor | None = None) -> Tensor | tuple[Tensor, Tensor]:
        _, sequence_length = input_ids.shape
        if sequence_length > self.config.context_size:
            raise ValueError("input sequence is longer than context_size")
        positions = torch.arange(sequence_length, device=input_ids.device)
        x = self.token_embedding(input_ids) + self.position_embedding(positions)
        mask = torch.triu(
            torch.ones(sequence_length, sequence_length, device=input_ids.device, dtype=torch.bool),
            diagonal=1,
        )
        for block in self.blocks:
            x = block(x, mask)
        logits = self.lm_head(self.final_norm(x))
        if labels is None:
            return logits
        loss = F.cross_entropy(
            logits[:, :-1].contiguous().view(-1, self.config.vocab_size),
            labels[:, 1:].contiguous().view(-1),
            ignore_index=-100,
        )
        return logits, loss


def save_checkpoint(model: TinyCausalLM, destination: str | Path, *, step: int = 0) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": asdict(model.config),
        "model": model.state_dict(),
        "step": step,
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