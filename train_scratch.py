"""Train the HWK Transformer from random weights, with no base model.

Two data modes:

1. Pretraining (token shards): --data points at a directory of uint16 .bin
   shards produced by tokenize_corpus.py (with manifest.json + eval.bin).
   Streams blocks with bounded memory, shuffles shard order per epoch.

2. SFT / small data: --data points at a .jsonl (messages format), .parquet
   (text column), or .txt file. Tokenizes on the fly, holds out a small eval
   split.

Checkpoints go to --output-dir; every save is mirrored to --mirror-dir (e.g.
X:). Resume with --resume, which continues from the latest checkpoint.pt and
its trainer-state.pt.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import shutil
import sys
import time
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Iterable, Iterator

# Must be set before torch allocates any CUDA memory: with several processes
# sharing the 8GB card (training + SD/whisper + desktop apps), the caching
# allocator fragments reserved memory and OOMs happen long before VRAM is
# truly exhausted. Expandable segments let a block grow instead of forcing a
# contiguous re-reservation.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

PROJECT_PACKAGE = Path(__file__).resolve().parent / "file-agent"
if str(PROJECT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE))
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import torch

from hwk_paths import TOKENIZER_DIR
from torch import Tensor
from torch.utils.data import DataLoader

from hwk_model import ByteTokenizer, ModelConfig, TinyCausalLM, save_checkpoint
from hwk_model.bpe_tokenizer import BpeTokenizer, load_bpe

LOG_FIELDS = [
    "step", "epoch", "tokens", "loss", "eval_loss", "perplexity",
    "lr", "tok_per_sec", "elapsed_min",
]


# ---------------------------------------------------------------------------
# Small-data (jsonl / parquet / txt) helpers — kept for evaluate_scratch.py
# ---------------------------------------------------------------------------

class TokenBlockDataset:
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
        # Next-token prediction: input tokens [0..ctx-1], targets [1..ctx].
        return source[:-1], source[1:]


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


def mean_loss(model: TinyCausalLM, loader: Iterable[tuple[Tensor, Tensor]], device: torch.device, dtype: str) -> float:
    model.eval()
    losses: list[float] = []
    autocast_dtype = torch.bfloat16 if dtype == "bf16" else torch.float16
    with torch.no_grad():
        for input_ids, labels in loader:
            with torch.autocast("cuda", dtype=autocast_dtype, enabled=device.type == "cuda"):
                _, loss = model(input_ids.to(device), labels.to(device))
            losses.append(float(loss.item()))
    model.train()
    return sum(losses) / max(1, len(losses))


def _device(value: str) -> torch.device:
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(value)


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Streaming pretraining on token shards
# ---------------------------------------------------------------------------

def iter_shard_blocks(shard_paths: list[Path], context: int, seed: int) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (inputs, targets) block pairs, cycling shards with shuffling."""
    rng = random.Random(seed)
    while True:
        order = list(shard_paths)
        rng.shuffle(order)
        for path in order:
            array = np.fromfile(path, dtype=np.uint16)
            if array.size < context + 2:
                continue
            start = rng.randint(0, max(0, array.size - context - 2))
            for pos in range(start, array.size - context - 1, context):
                yield array[pos : pos + context], array[pos + 1 : pos + context + 1]


def iter_eval_blocks(eval_path: Path, context: int, max_blocks: int) -> Iterator[tuple[Tensor, Tensor]]:
    array = np.fromfile(eval_path, dtype=np.uint16)
    total_positions = len(array) - context - 1
    if total_positions <= 0:
        return
    # _tokens_from_dir() concatenates each selected corpus's own eval.bin, in
    # alphabetical order, into one combined_eval.bin. Sampling sequentially
    # from position 0 (the old behaviour) only ever reaches into the FIRST
    # corpus in that order and only the start of it - e.g. with corpora
    # "arabic,pile", combined_eval.bin is [arabic tail | pile tail], and the
    # old code's block window never got past the first ~64k tokens of
    # "arabic", so eval loss silently measured 100% Arabic, 0% English, no
    # matter what --corpora was trained on. Spreading start positions evenly
    # across the whole file gives every source proportional coverage.
    n_blocks = min(max_blocks, total_positions // context + 1)
    if n_blocks <= 0:
        return
    stride = total_positions / n_blocks
    for i in range(n_blocks):
        pos = int(i * stride)
        inputs = torch.tensor(array[pos : pos + context], dtype=torch.long)
        targets = torch.tensor(array[pos + 1 : pos + context + 1], dtype=torch.long)
        yield inputs, targets


def _tokens_from_dir(
    data_dir: Path, corpora: list[str] | None = None
) -> tuple[list[Path], Path | None]:
    """Collect shards from a flat dir or a root of per-corpus sub-directories.

    tokenize_corpus.py writes one sub-directory per corpus (each with its own
    manifest + eval.bin). When a root is given, all selected corpora's train
    shards are pooled and shuffled together each epoch, and their eval.bin
    tails are combined into one evaluation file so eval loss stays meaningful
    across the mixture.
    """
    flat = sorted(data_dir.glob("train_*.bin"))
    if flat:
        eval_path = data_dir / "eval.bin"
        return flat, eval_path if eval_path.exists() else None
    subdirs: list[Path] = []
    for candidate in sorted(data_dir.iterdir()):
        if not candidate.is_dir():
            continue
        if corpora and candidate.name not in corpora:
            continue
        if sorted(candidate.glob("train_*.bin")):
            subdirs.append(candidate)
    if not subdirs:
        raise SystemExit(
            f"No train_*.bin shards found in {data_dir}"
            + (f" for corpora {corpora}" if corpora else "")
        )
    shards = [
        shard for corpus in subdirs for shard in sorted(corpus.glob("train_*.bin"))
    ]
    eval_path = data_dir / "combined_eval.bin"
    eval_sources = [corpus / "eval.bin" for corpus in subdirs if (corpus / "eval.bin").exists()]
    if eval_sources:
        rebuild = not eval_path.exists()
        if not rebuild:
            newest_source = max(source.stat().st_mtime for source in eval_sources)
            rebuild = newest_source > eval_path.stat().st_mtime
        if rebuild:
            combined = np.concatenate(
                [np.fromfile(source, dtype=np.uint16) for source in eval_sources]
            )
            combined.tofile(eval_path)
    return shards, eval_path if eval_path.exists() else None


# ---------------------------------------------------------------------------
# SFT loader (jsonl messages)
# ---------------------------------------------------------------------------

class SftDataset:
    def __init__(self, records: list[dict[str, object]], tokenizer, context: int, seed: int) -> None:
        self.context = context
        self.rng = random.Random(seed)
        self.sequences: list[list[int]] = []
        for record in records:
            ids = tokenizer.encode(_message_text(record))
            if len(ids) > context:
                ids = ids[-context:]  # keep the assistant tail (final answer)
            self.sequences.append(ids)
        if not self.sequences:
            raise SystemExit("SFT dataset is empty after filtering.")

    def __len__(self) -> int:
        return len(self.sequences)

    def batches(self, batch_size: int, pad_id: int) -> Iterator[tuple[Tensor, Tensor]]:
        order = list(range(len(self.sequences)))
        while True:
            self.rng.shuffle(order)
            for start in range(0, len(order), batch_size):
                chunk = [self.sequences[i] for i in order[start : start + batch_size]]
                width = max(len(seq) for seq in chunk)
                inputs = torch.full((len(chunk), width), pad_id, dtype=torch.long)
                targets = torch.full((len(chunk), width), -100, dtype=torch.long)
                for row, seq in enumerate(chunk):
                    inputs[row, : len(seq)] = torch.tensor(seq, dtype=torch.long)
                    targets[row, : len(seq)] = torch.tensor(seq, dtype=torch.long)
                yield inputs, targets


def load_jsonl_records(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# Checkpointing with trainer state
# ---------------------------------------------------------------------------

def save_trainer_state(
    output_dir: Path,
    model: TinyCausalLM,
    optimizer: torch.optim.Optimizer,
    scheduler,
    *,
    step: int,
    epoch: int,
    tokens: int,
    tokenizer,
    seed: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    save_checkpoint(model, output_dir / "checkpoint.pt", step=step, tokenizer=tokenizer)
    torch.save(
        {
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict() if scheduler is not None else None,
            "step": step,
            "epoch": epoch,
            "tokens": tokens,
            "seed": seed,
            "tokenizer": {
                "type": "bpe" if isinstance(tokenizer, BpeTokenizer) else "byte",
                "model_path": getattr(tokenizer, "model_path", None),
                "vocab_size": tokenizer.vocab_size,
                "pad_id": tokenizer.pad_id,
                "bos_id": tokenizer.bos_id,
                "eos_id": tokenizer.eos_id,
            },
        },
        output_dir / "trainer-state.pt",
    )
    # final.pt is the model-only checkpoint the agent loads.
    save_checkpoint(model, output_dir / "final.pt", step=step, tokenizer=tokenizer)


def mirror_checkpoints(output_dir: Path, mirror_dir: Path | None) -> None:
    if mirror_dir is None:
        return
    mirror_dir.mkdir(parents=True, exist_ok=True)
    for name in ("checkpoint.pt", "trainer-state.pt", "final.pt"):
        source = output_dir / name
        if source.exists():
            destination = mirror_dir / name
            if not destination.exists() or destination.stat().st_size != source.stat().st_size:
                shutil.copy2(source, destination)


def load_tokenizer_from_state(state: dict[str, object], fallback: str | None) -> ByteTokenizer | BpeTokenizer:
    meta = state.get("tokenizer")
    if isinstance(meta, dict) and meta.get("type") == "bpe" and meta.get("model_path"):
        try:
            return load_bpe(meta["model_path"])
        except Exception:
            pass
    if fallback:
        try:
            return load_bpe(fallback)
        except Exception:
            pass
    return ByteTokenizer()


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _collate(blocks: list[tuple[np.ndarray, np.ndarray]]) -> tuple[Tensor, Tensor]:
    inputs = torch.stack([torch.as_tensor(x, dtype=torch.long) for x, _ in blocks])
    targets = torch.stack([torch.as_tensor(y, dtype=torch.long) for _, y in blocks])
    return inputs, targets


def train(args: argparse.Namespace) -> None:
    if args.context < 16 or args.d_model % args.heads:
        raise SystemExit("--context must be >= 16 and --d-model must be divisible by --heads.")
    if args.dtype not in ("fp16", "bf16", "fp32"):
        raise SystemExit("--dtype must be fp16, bf16, or fp32")
    if args.data is None:
        raise SystemExit("--data is required")

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    device = _device(args.device)
    print(f"device={device} dtype={args.dtype} data={args.data}")

    data_path = Path(args.data)
    if data_path.is_dir():
        shard_paths, eval_path = _tokens_from_dir(data_path, args.corpora)
        mode = "pretrain"
    else:
        if data_path.suffix == ".jsonl":
            records = load_jsonl_records(data_path)
            eval_count = max(1, int(len(records) * 0.05))
            rng = random.Random(args.seed)
            rng.shuffle(records)
            eval_records = records[:eval_count]
            train_records = records[eval_count:]
            print(f"SFT: {len(train_records)} train / {len(eval_records)} eval records")
        else:
            train_records = eval_records = None
        mode = "sft" if train_records is not None else "text"
        shard_paths = []
        eval_path = None

    # Tokenizer
    # The byte tokenizer (vocab 259) is only correct for checkpoints trained
    # with it. Every serious run uses the project BPE; defaulting SFT/text
    # mode to it (when the model file exists) prevents a silent
    # byte-vs-BPE mismatch that would corrupt resume checks and eval loss.
    if args.tokenizer is None and mode != "pretrain":
        default_bpe = TOKENIZER_DIR / "hwk_spm.model"
        if default_bpe.exists():
            args.tokenizer = str(default_bpe)

    tokenizer: ByteTokenizer | BpeTokenizer
    if mode == "pretrain":
        if args.tokenizer:
            tokenizer = load_bpe(args.tokenizer)
        else:
            tokenizer = ByteTokenizer()
    else:
        tokenizer = load_bpe(args.tokenizer) if args.tokenizer else ByteTokenizer()
    print(f"tokenizer: {type(tokenizer).__name__} vocab={tokenizer.vocab_size}")

    config = ModelConfig(
        vocab_size=tokenizer.vocab_size,
        context_size=args.context,
        d_model=args.d_model,
        n_heads=args.heads,
        n_layers=args.layers,
        dropout=args.dropout,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    resume_step = 0
    resume_tokens = 0
    resume_epoch = 0
    model = TinyCausalLM(config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scheduler = None

    if args.resume:
        checkpoint = output_dir / "checkpoint.pt"
        state_path = output_dir / "trainer-state.pt"
        if checkpoint.exists() and state_path.exists():
            payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
            saved_config = ModelConfig(**payload["config"])
            # context_size is exempt: the model uses RoPE (see hwk_model/model.py),
            # not learned position embeddings, so no weight tensor's shape depends
            # on it. Changing it is a training-time choice (longer sequences =
            # more positions the rotary embedding is evaluated at), not an
            # architecture change, so a checkpoint trained at one context can be
            # resumed/continued at a different (typically larger) one.
            if _dc_replace(saved_config, context_size=config.context_size) != config:
                hint = ""
                if saved_config.vocab_size != config.vocab_size:
                    hint = " (vocab_size differs - are you passing the same --tokenizer the checkpoint was trained with?)"
                raise SystemExit(
                    f"Resume config mismatch: {saved_config} != {config}.{hint} "
                    "Use a fresh output dir for new hyperparameters."
                )
            if saved_config.context_size != config.context_size:
                print(
                    f"context extension: {saved_config.context_size} -> {config.context_size} "
                    "(RoPE-based model, no architecture change)"
                )
            model.load_state_dict(payload["model"])
            state = torch.load(state_path, map_location="cpu", weights_only=False)
            optimizer.load_state_dict(state["optimizer"])
            resume_step = int(state.get("step", 0))
            resume_tokens = int(state.get("tokens", 0))
            resume_epoch = int(state.get("epoch", 0))
            print(f"resumed from step={resume_step} tokens={resume_tokens:,}")

    model.gradient_checkpointing = args.grad_checkpoint
    model.to(device)
    for opt_state in optimizer.state.values():
        for key, value in opt_state.items():
            if isinstance(value, torch.Tensor):
                opt_state[key] = value.to(device)
    optimizer.zero_grad(set_to_none=True)

    autocast_dtype = torch.bfloat16 if args.dtype == "bf16" else torch.float16
    use_scaler = args.dtype == "fp16" and device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_scaler)

    # Data iterators
    # Eval batch scales down at longer contexts: 8 blocks x 4096 tokens is a
    # much bigger inference batch than the training batch itself, and was the
    # OOM that killed the first 1024->4096 attempt (sft_training.log).
    eval_batch_size = max(1, min(8, 8192 // max(1, args.context)))
    if mode == "pretrain":
        block_iter = iter_shard_blocks(shard_paths, args.context, args.seed)
        eval_blocks = list(iter_eval_blocks(eval_path, args.context, max_blocks=64)) if eval_path else []
        eval_loader = [
            _collate(eval_blocks[start : start + eval_batch_size])
            for start in range(0, len(eval_blocks), eval_batch_size)
        ] if eval_blocks else []
        if not eval_loader:
            print("warning: no eval.bin found; eval loss will be skipped")
    else:
        if train_records is not None:
            dataset = SftDataset(train_records, tokenizer, args.context, args.seed)
            eval_sft = SftDataset(eval_records, tokenizer, args.context, args.seed)
            eval_loader = [next(eval_sft.batches(eval_batch_size, tokenizer.pad_id))]
        else:
            tokens = tokenizer.encode(load_text(data_path))
            train_data, eval_data = split_tokens(tokens, args.context)
            eval_loader = [next(iter(DataLoader(eval_data, batch_size=eval_batch_size)))]
            dataset = None

    log_path = output_dir / "training_log.csv"
    fresh_log = not log_path.exists()
    log_file = log_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(log_file, fieldnames=LOG_FIELDS)
    if fresh_log:
        writer.writeheader()

    global_step = resume_step
    tokens_seen = resume_tokens
    epoch = resume_epoch
    started = time.time()
    loss_accum = 0.0
    step_tokens = args.batch_size * args.gradient_accumulation * args.context
    micro_batches = 0
    optimizer.zero_grad(set_to_none=True)

    def eval_loss() -> float:
        if not eval_loader:
            return float("nan")
        losses: list[float] = []
        model.eval()
        with torch.no_grad():
            for inputs, targets in eval_loader:
                try:
                    with torch.autocast("cuda", dtype=autocast_dtype, enabled=device.type == "cuda"):
                        _, loss = model(inputs.to(device), targets.to(device))
                except torch.OutOfMemoryError:
                    # A transient co-tenant spike must not kill a 12-hour run
                    # over one eval batch: drop the cached blocks, skip this
                    # batch, and continue with the rest.
                    torch.cuda.empty_cache()
                    print("warning: eval batch OOM - skipped", flush=True)
                    continue
                losses.append(float(loss.item()))
        model.train()
        return sum(losses) / max(1, len(losses)) if losses else float("nan")

    def make_block_stream() -> Iterator[tuple[Tensor, Tensor]]:
        while True:
            if mode == "pretrain":
                batch = []
                for _ in range(args.batch_size):
                    x, y = next(block_iter)
                    batch.append((x, y))
                yield _collate(batch)
            else:
                assert dataset is not None
                yield from dataset.batches(args.batch_size, tokenizer.pad_id)

    block_stream = make_block_stream()

    while global_step < args.max_steps:
        # Gather one optimizer step worth of micro-batches.
        micro_batches = 0
        loss_accum = 0.0
        optimizer.zero_grad(set_to_none=True)
        for _ in range(args.gradient_accumulation):
            inputs, targets = next(block_stream)
            inputs, targets = inputs.to(device), targets.to(device)
            try:
                with torch.autocast("cuda", dtype=autocast_dtype, enabled=device.type == "cuda"):
                    _, loss = model(inputs, targets)
                    scaled = loss / args.gradient_accumulation
                scaler.scale(scaled).backward()
            except torch.OutOfMemoryError:
                # Kill the half-built gradients cleanly, free the cached
                # blocks, and retry this micro-batch once. This is what saved
                # runs from desktop apps / browser tabs grabbing a few hundred
                # MB mid-run (the exact crash at step 27,100 on 2026-09-06).
                optimizer.zero_grad(set_to_none=True)
                torch.cuda.empty_cache()
                print("warning: training micro-batch OOM - retrying once", flush=True)
                time.sleep(5.0)
                try:
                    with torch.autocast("cuda", dtype=autocast_dtype, enabled=device.type == "cuda"):
                        _, loss = model(inputs, targets)
                        scaled = loss / args.gradient_accumulation
                    scaler.scale(scaled).backward()
                except torch.OutOfMemoryError:
                    optimizer.zero_grad(set_to_none=True)
                    torch.cuda.empty_cache()
                    save_trainer_state(
                        output_dir, model, optimizer, scheduler,
                        step=global_step, epoch=epoch, tokens=tokens_seen, tokenizer=tokenizer, seed=args.seed,
                    )
                    raise SystemExit(
                        f"CUDA OOM twice at step {global_step} even after emptying the cache. "
                        "Close other GPU apps or reduce --batch-size; checkpoint was saved, so "
                        "this run resumes cleanly with --resume."
                    ) from None
            loss_accum += float(loss.item())
            micro_batches += 1
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
        scaler.step(optimizer)
        scaler.update()
        # Linear LR warmup, then cosine decay to ~10% of peak (pitfall guard:
        # constant LR after warmup converges worse and can oscillate late).
        import math as _math

        warmup_scale = min(1.0, global_step / max(1, args.warmup_steps))
        progress = max(0, global_step - args.warmup_steps) / max(1, args.max_steps - args.warmup_steps)
        cosine_scale = 0.1 + 0.9 * 0.5 * (1 + _math.cos(_math.pi * min(1.0, progress)))
        for group in optimizer.param_groups:
            group["lr"] = args.learning_rate * warmup_scale * cosine_scale
        if scheduler is not None:
            scheduler.step()

        global_step += 1
        tokens_seen += step_tokens
        loss_accum /= max(1, micro_batches)

        if global_step % args.log_steps == 0:
            elapsed = (time.time() - started) / 60
            tok_rate = tokens_seen / max(1, time.time() - started)
            eval_loss_value = eval_loss()
            row = {
                "step": global_step,
                "epoch": epoch,
                "tokens": tokens_seen,
                "loss": round(loss_accum, 4),
                "eval_loss": round(eval_loss_value, 4) if eval_loss_value == eval_loss_value else "",
                "perplexity": round(math.exp(min(eval_loss_value, 20)), 2) if eval_loss_value == eval_loss_value else "",
                "lr": optimizer.param_groups[0]["lr"],
                "tok_per_sec": round(tok_rate),
                "elapsed_min": round(elapsed, 1),
            }
            writer.writerow(row)
            log_file.flush()
            remaining = (args.max_steps - global_step) * step_tokens / max(1, tok_rate) / 3600
            print(
                f"step={global_step} loss={loss_accum:.4f} eval_loss={row['eval_loss']} "
                f"tok/s={tok_rate:,.0f} elapsed={elapsed:.1f}min eta={remaining:.1f}h",
                flush=True,
            )

        if global_step % args.save_steps == 0:
            save_trainer_state(
                output_dir, model, optimizer, scheduler,
                step=global_step, epoch=epoch, tokens=tokens_seen, tokenizer=tokenizer, seed=args.seed,
            )
            mirror_checkpoints(output_dir, Path(args.mirror_dir) if args.mirror_dir else None)
            print(f"checkpoint saved: {output_dir} step={global_step}", flush=True)

    log_file.close()
    save_trainer_state(
        output_dir, model, optimizer, scheduler,
        step=global_step, epoch=epoch, tokens=tokens_seen, tokenizer=tokenizer, seed=args.seed,
    )
    mirror_checkpoints(output_dir, Path(args.mirror_dir) if args.mirror_dir else None)
    print(f"done: {output_dir}/ step={global_step} tokens={tokens_seen:,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train HWK from scratch (pretraining or SFT).")
    parser.add_argument("--data", default="data/agent_instructions.jsonl")
    parser.add_argument("--corpora", default=None, help="Comma list of corpus dirs to train on (default: all).")
    parser.add_argument("--tokenizer", default=None, help="Path to BPE .model; default byte tokenizer.")
    parser.add_argument("--output-dir", default="D:/hwk-models/scratch")
    parser.add_argument("--mirror-dir", default="X:/hwk-backups/scratch")
    parser.add_argument("--context", type=int, default=512)
    parser.add_argument("--d-model", type=int, default=768)
    parser.add_argument("--heads", type=int, default=12)
    parser.add_argument("--layers", type=int, default=12)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=200_000)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--warmup-steps", type=int, default=500)
    parser.add_argument("--weight-decay", type=float, default=0.1)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument("--save-steps", type=int, default=1000)
    parser.add_argument("--eval-steps", type=int, default=250)
    parser.add_argument("--log-steps", type=int, default=25)
    parser.add_argument("--dtype", choices=["fp16", "bf16", "fp32"], default="fp16")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--grad-checkpoint", action="store_true",
        help="Trade compute for memory (recompute activations on backward) to fit a larger --context in less VRAM.",
    )
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    # Legacy epochs flag: ignore (superseded by --max-steps) but keep accepted.
    args.corpora = [c.strip() for c in args.corpora.split(",") if c.strip()] if args.corpora else None
    train(args)


if __name__ == "__main__":
    _utf8_stdio()
    main()