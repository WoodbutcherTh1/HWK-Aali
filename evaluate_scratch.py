"""Evaluate a from-scratch HWK checkpoint.

Data can be a token-shard directory (evaluates eval.bin) or a .jsonl/.txt
file (uses the 90/10 token split, as before). The tokenizer recorded in the
checkpoint is used automatically (BPE or byte).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

PROJECT_PACKAGE = Path(__file__).resolve().parent / "file-agent"
if str(PROJECT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE))

import numpy as np
import torch
from torch.utils.data import DataLoader

from hwk_model import ByteTokenizer, load_checkpoint, load_bpe
from train_scratch import TokenBlockDataset, load_text


def eval_block_loader(eval_path: Path, context: int, batch_size: int):
    array = np.fromfile(eval_path, dtype=np.uint16)
    blocks = []
    for pos in range(0, len(array) - context - 1, context):
        inputs = torch.tensor(array[pos : pos + context], dtype=torch.long)
        targets = torch.tensor(array[pos + 1 : pos + context + 1], dtype=torch.long)
        blocks.append((inputs, targets))
    if not blocks:
        raise SystemExit(f"eval.bin too small for context {context}: {array.size} tokens")
    for start in range(0, len(blocks), batch_size):
        yield torch.stack([x for x, _ in blocks[start : start + batch_size]]), torch.stack(
            [y for _, y in blocks[start : start + batch_size]]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a scratch HWK model.")
    parser.add_argument("--checkpoint", default="D:/hwk-models/scratch/final.pt")
    parser.add_argument("--data", default=None, help="Token-shard dir with eval.bin, or a .jsonl/.txt file.")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--dtype", choices=["fp16", "bf16", "fp32"], default="fp16")
    args = parser.parse_args()

    device = torch.device(
        "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    )
    model, payload = load_checkpoint(args.checkpoint, device=device)
    context = model.config.context_size

    tokenizer_meta = payload.get("tokenizer") or {}
    if tokenizer_meta.get("type") == "bpe" and tokenizer_meta.get("model_path"):
        tokenizer = load_bpe(tokenizer_meta["model_path"])
    else:
        tokenizer = ByteTokenizer()

    autocast_dtype = torch.bfloat16 if args.dtype == "bf16" else torch.float16
    losses: list[float] = []

    if args.data and Path(args.data).is_dir():
        eval_path = Path(args.data) / "eval.bin"
        if not eval_path.exists():
            raise SystemExit(f"no eval.bin in {args.data}")
        loader = eval_block_loader(eval_path, context, args.batch_size)
        with torch.no_grad():
            for inputs, targets in loader:
                with torch.autocast("cuda", dtype=autocast_dtype, enabled=device.type == "cuda"):
                    _, loss = model(inputs.to(device), targets.to(device))
                losses.append(float(loss.item()))
    else:
        data = args.data or "data/agent_instructions.jsonl"
        tokens = tokenizer.encode(load_text(data))
        split_at = max(context + 2, int(len(tokens) * 0.9))
        evaluation = TokenBlockDataset(tokens[split_at:], context)
        loader = DataLoader(evaluation, batch_size=args.batch_size)
        with torch.no_grad():
            for input_ids, labels in loader:
                with torch.autocast("cuda", dtype=autocast_dtype, enabled=device.type == "cuda"):
                    _, loss = model(input_ids.to(device), labels.to(device))
                losses.append(float(loss.item()))

    loss = sum(losses) / max(1, len(losses))
    print(f"tokenizer={type(tokenizer).__name__} vocab={tokenizer.vocab_size}")
    print(f"checkpoint_step={payload.get('step', 0)}")
    print(f"loss={loss:.4f}")
    print(f"perplexity={math.exp(min(loss, 20)):.4f}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()