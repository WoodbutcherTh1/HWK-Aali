"""Train the HWK Transformer from random weights, with no base model."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Iterable

PROJECT_PACKAGE = Path(__file__).resolve().parent / "file-agent"
if str(PROJECT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE))

import torch
from torch import Tensor
from torch.utils.data import DataLoader, Dataset

from hwk_model import ByteTokenizer, ModelConfig, TinyCausalLM, save_checkpoint


class TokenBlockDataset(Dataset[tuple[Tensor, Tensor]]):
    def __init__(self, tokens: list[int], context_size: int) -> None:
        if len(tokens) < context_size + 2:
            raise ValueError(
                f"Dataset has {len(tokens)} tokens; at least {context_size + 2} are required."
            )
        self.tokens = torch.tensor(tokens, dtype=torch.long)
        self.context_size = context_size
        self.starts = list(range(0, len(tokens) - context_size - 1, context_size))
        if not self.starts:
            raise ValueError("Dataset did not produce any training blocks.")

    def __len__(self) -> int:
        return len(self.starts)

    def __getitem__(self, index: int) -> tuple[Tensor, Tensor]:
        start = self.starts[index]
        source = self.tokens[start : start + self.context_size + 1]
        return source[:-1], source


def _message_text(record: dict[str, object]) -> str:
    messages = record.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Each JSONL record must contain a messages list")
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Each message must be an object")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("Each message needs string role and content")
        parts.append(f"{role.capitalize()}: {content}")
    return "\n".join(parts) + "\n"


def load_text(path: str | Path) -> str:
    source = Path(path)
    if source.suffix == ".jsonl":
        texts: list[str] = []
        with source.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        texts.append(_message_text(json.loads(line)))
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise ValueError(f"Invalid training record on line {line_number}") from exc
        return "\n".join(texts)
    if source.suffix == ".parquet":
        try:
            import pandas as pd
        except ImportError as exc:
            raise SystemExit("pandas and pyarrow are required for Parquet training data.") from exc
        frame = pd.read_parquet(source, columns=["text"])
        return "\n".join(str(value) for value in frame["text"].dropna())
    return source.read_text(encoding="utf-8")


def split_tokens(tokens: list[int], context_size: int) -> tuple[TokenBlockDataset, TokenBlockDataset]:
    split_at = max(context_size + 2, int(len(tokens) * 0.9))
    split_at = min(split_at, len(tokens) - context_size - 1)
    train = TokenBlockDataset(tokens[:split_at], context_size)
    evaluation = TokenBlockDataset(tokens[split_at:], context_size)
    return train, evaluation


def mean_loss(model: TinyCausalLM, loader: DataLoader, device: torch.device) -> float:
    model.eval()
    losses: list[float] = []
    with torch.no_grad():
        for input_ids, labels in loader:
            _, loss = model(input_ids.to(device), labels.to(device))
            losses.append(float(loss.item()))
    model.train()
    return sum(losses) / max(1, len(losses))


def _device(value: str) -> torch.device:
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train HWK from scratch.")
    parser.add_argument("--data", default="data/agent_instructions.jsonl")
    parser.add_argument("--output-dir", default="model/scratch")
    parser.add_argument("--context", type=int, default=256)
    parser.add_argument("--d-model", type=int, default=256)
    parser.add_argument("--heads", type=int, default=8)
    parser.add_argument("--layers", type=int, default=6)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--save-steps", type=int, default=500)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    if args.context < 16 or args.d_model % args.heads:
        raise SystemExit("--context must be >= 16 and --d-model must be divisible by --heads.")
    tokenizer = ByteTokenizer()
    tokens = tokenizer.encode(load_text(args.data))
    train_data, eval_data = split_tokens(tokens, args.context)
    device = _device(args.device)
    config = ModelConfig(
        context_size=args.context,
        d_model=args.d_model,
        n_heads=args.heads,
        n_layers=args.layers,
        dropout=args.dropout,
    )
    model = TinyCausalLM(config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    train_loader = DataLoader(train_data, batch_size=args.batch_size, shuffle=True)
    eval_loader = DataLoader(eval_data, batch_size=args.batch_size)
    scaler = torch.cuda.amp.GradScaler(enabled=device.type == "cuda")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path("scratch_training_log.csv")
    with log_path.open("w", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=["step", "epoch", "loss", "eval_loss", "perplexity"])
        writer.writeheader()
        global_step = 0
        model.train()
        for epoch in range(1, args.epochs + 1):
            optimizer.zero_grad(set_to_none=True)
            for batch_index, (input_ids, labels) in enumerate(train_loader, 1):
                input_ids, labels = input_ids.to(device), labels.to(device)
                with torch.autocast(
                    device_type="cuda",
                    dtype=torch.float16,
                    enabled=device.type == "cuda",
                ):
                    _, loss = model(input_ids, labels)
                    scaled_loss = loss / args.gradient_accumulation
                scaler.scale(scaled_loss).backward()
                if batch_index % args.gradient_accumulation and batch_index != len(train_loader):
                    continue
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                global_step += 1
                if global_step % args.save_steps == 0:
                    save_checkpoint(model, output_dir / "checkpoint.pt", step=global_step)
                eval_loss = mean_loss(model, eval_loader, device)
                writer.writerow({
                    "step": global_step,
                    "epoch": epoch,
                    "loss": float(loss.item()),
                    "eval_loss": eval_loss,
                    "perplexity": math.exp(min(eval_loss, 20)),
                })
                log_file.flush()
            print(f"epoch={epoch} step={global_step} eval_loss={eval_loss:.4f}")
    save_checkpoint(model, output_dir / "final.pt", step=global_step)
    print(f"Scratch model saved to {output_dir}/ on {device}.")


if __name__ == "__main__":
    main()