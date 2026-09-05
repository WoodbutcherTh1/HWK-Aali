"""Evaluate a from-scratch HWK checkpoint."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

PROJECT_PACKAGE = Path(__file__).resolve().parent / "file-agent"
if str(PROJECT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE))

import torch
from torch.utils.data import DataLoader

from hwk_model import ByteTokenizer, load_checkpoint
from train_scratch import TokenBlockDataset, load_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a scratch HWK model.")
    parser.add_argument("--checkpoint", default="model/scratch/final.pt")
    parser.add_argument("--data", default="data/agent_instructions.jsonl")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    device = torch.device(
        "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    )
    model, payload = load_checkpoint(args.checkpoint, device=device)
    tokenizer = ByteTokenizer()
    tokens = tokenizer.encode(load_text(args.data))
    context = model.config.context_size
    split_at = max(context + 2, int(len(tokens) * 0.9))
    evaluation = TokenBlockDataset(tokens[split_at:], context)
    loader = DataLoader(evaluation, batch_size=args.batch_size)
    losses = []
    with torch.no_grad():
        for input_ids, labels in loader:
            _, loss = model(input_ids.to(device), labels.to(device))
            losses.append(float(loss.item()))
    loss = sum(losses) / max(1, len(losses))
    print(f"checkpoint_step={payload.get('step', 0)}")
    print(f"loss={loss:.4f}")
    print(f"perplexity={math.exp(min(loss, 20)):.4f}")


if __name__ == "__main__":
    main()