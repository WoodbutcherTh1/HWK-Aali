"""Bootstrap an SFT-ready trainer state from the Phase B pretrained model.

Phase B (pretraining, 100,000 steps / 3.28B tokens on the pile+arabic corpora
with OUR OWN tokenizer) left final.pt / checkpoint.pt / trainer-state.pt at
D:/hwk-models/context-4k. train_scratch.py --resume restores weights AND
optimizer AND step from that dir - but SFT needs the pretrained WEIGHTS with
a FRESH optimizer and step=0 (the LR cosine schedule is derived from
--max-steps, and resuming with step=100,000 would instantly satisfy
max_steps=5000 and end before touching a single batch).

This script lifts ONLY the model weights (+ tokenizer metadata) from the
Phase B checkpoint into a NEW output dir, with step=0 / epoch=0 / tokens=0
and no optimizer state - then train_scratch.py resumes from it with the SFT
mix and its own schedule:

    .venv/Scripts/python.exe scripts/bootstrap_sft_state.py \
        --source D:/hwk-models/context-4k \
        --target D:/hwk-models/aali-sft-4k
    .venv/Scripts/python.exe train_scratch.py \
        --data D:/hwk-data/soup/sft_v2.jsonl \
        --tokenizer D:/hwk-data/tokenizer/hwk_spm.model \
        --output-dir D:/hwk-models/aali-sft-4k \
        --context 4096 --d-model 768 --heads 12 --layers 12 \
        --batch-size 1 --gradient-accumulation 8 --grad-checkpoint \
        --max-steps 5000 --save-steps 250 --log-steps 10 \
        --learning-rate 2e-5 --warmup-steps 100 --dtype bf16 --resume

Config is verified BEFORE writing anything: vocab/d_model/heads/layers must
match the live --tokenizer + flags (context is RoPE-exempt). The source
checkpoint is never modified.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
# hwk_model lives under file-agent/ (same sys.path contract as train_scratch.py)
sys.path.insert(0, str(ROOT / "file-agent"))
sys.path.insert(0, str(ROOT))

from hwk_model import ModelConfig, TinyCausalLM  # noqa: E402
from hwk_model.bpe_tokenizer import load_bpe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True,
                        help="Phase B output dir (checkpoint.pt + trainer-state.pt)")
    parser.add_argument("--target", required=True,
                        help="NEW dir for the SFT bootstrapped state")
    parser.add_argument("--tokenizer", default="D:/hwk-data/tokenizer/hwk_spm.model")
    parser.add_argument("--d-model", type=int, default=768)
    parser.add_argument("--heads", type=int, default=12)
    parser.add_argument("--layers", type=int, default=12)
    args = parser.parse_args()

    source = Path(args.source)
    target = Path(args.target)
    checkpoint_path = source / "checkpoint.pt"
    if not checkpoint_path.exists():
        print(f"FAILED: no checkpoint at {checkpoint_path}")
        return 1
    if target.exists() and (target / "checkpoint.pt").exists():
        print(f"FAILED: {target} already holds a checkpoint - refusing to overwrite")
        return 1

    payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    saved = payload.get("config", {})
    tokenizer = load_bpe(args.tokenizer)
    config = ModelConfig(
        vocab_size=tokenizer.vocab_size,
        context_size=int(saved.get("context_size", 4096)),
        d_model=args.d_model,
        n_heads=args.heads,
        n_layers=args.layers,
        dropout=0.0,  # inference-time value; trainer sets its own
    )
    mismatches = [key for key in ("vocab_size", "d_model", "n_heads", "n_layers")
                  if key in saved and saved[key] != getattr(config, key)]
    if mismatches:
        print(f"FAILED: config mismatch on {mismatches}: "
              f"checkpoint={saved} vs requested={config}")
        return 1
    print(f"source config ok: {saved}")
    print(f"target config:    {config}")

    # Instantiate at the saved config and re-save that config VERBATIM:
    # train_scratch.py --resume compares the checkpoint config field-by-field
    # against the args-built one, so any drift (e.g. dropout 0.0 vs the
    # trainer's default 0.1) is a SystemExit at launch. Writing the saved
    # config as-is keeps the contract: run the trainer with the same
    # --d-model/--heads/--layers/--dropout (defaults match Phase B).
    saved_model_config = ModelConfig(**saved)
    model = TinyCausalLM(saved_model_config)
    model.load_state_dict(payload["model"], strict=True)
    config_out = vars(saved_model_config)
    print(f"weights loaded: {sum(p.numel() for p in model.parameters()):,} params, "
          f"step={payload.get('step')}")

    tokenizer_meta = payload.get("tokenizer", {
        "type": "bpe", "model_path": args.tokenizer,
        "vocab_size": tokenizer.vocab_size, "pad_id": tokenizer.pad_id,
        "bos_id": tokenizer.bos_id, "eos_id": tokenizer.eos_id})
    target.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"model": model.state_dict(), "config": config_out,
         "step": 0, "tokenizer": tokenizer_meta},
        target / "checkpoint.pt")
    torch.save(
        {"optimizer": None, "scheduler": None, "step": 0, "epoch": 0,
         "tokens": 0, "seed": 1337, "tokenizer": tokenizer_meta},
        target / "trainer-state.pt")
    # final.pt is the model-only artifact the agent's loader expects.
    torch.save({"model": model.state_dict(), "config": config_out,
                "step": 0, "tokenizer": tokenizer_meta},
               target / "final.pt")
    print(f"SFT state bootstrapped at {target}: pretrained weights, fresh "
          f"optimizer, step=0 - ready for train_scratch.py --resume")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
