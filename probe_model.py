"""Generate sample responses from a scratch checkpoint.

Probes: Arabic greeting, English greeting, file-tool request, code, medical,
and law prompts. Shows the raw continuation so progress is visible as training
advances.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_PACKAGE = Path(__file__).resolve().parent / "file-agent"
if str(PROJECT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE))

import torch

from hwk_model import ByteTokenizer, load_checkpoint, load_bpe
from hwk_model.generation import generate_text

PROBES = [
    ("greeting_ar", "User: مرحبا\nAssistant:"),
    ("greeting_en", "User: hello\nAssistant:"),
    ("tool_call", "System: You are a safe file assistant. Available tools: list_files, read_file, write_file.\nUser: أنشئ ملف notes/today.txt واكتب فيه مرحباً\nAssistant:"),
    ("code", "User: اكتب دالة بايثون تجمع رقمين\nAssistant:"),
    ("code_en", "User: Write a Python function that adds two numbers\nAssistant:"),
    ("medical", "User: What is a fever?\nAssistant:"),
    ("law", "User: What is a contract?\nAssistant:"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe a scratch checkpoint with sample generations.")
    parser.add_argument("--checkpoint", default="D:/hwk-models/scratch/final.pt")
    parser.add_argument("--max-new-tokens", type=int, default=100)
    parser.add_argument("--temperature", type=float, default=0.5)
    parser.add_argument("--top-k", type=int, default=40)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    device = torch.device(
        "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    )
    model, payload = load_checkpoint(args.checkpoint, device=device)
    tokenizer_meta = payload.get("tokenizer") or {}
    if tokenizer_meta.get("type") == "bpe" and tokenizer_meta.get("model_path"):
        tokenizer = load_bpe(tokenizer_meta["model_path"])
    else:
        tokenizer = ByteTokenizer()

    print(f"checkpoint step={payload.get('step', 0)} tokenizer={type(tokenizer).__name__} device={device}\n")
    for name, prompt in PROBES:
        output = generate_text(
            model,
            tokenizer,
            prompt,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_k=args.top_k,
        )
        print(f"--- {name} ---")
        print(output.strip() or "(empty)")
        print()


if __name__ == "__main__":
    main()