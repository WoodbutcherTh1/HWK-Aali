"""Evaluate a local causal language model and report perplexity."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the local model.")
    parser.add_argument("--model", default="model")
    parser.add_argument("--data", default="processed_data/cleaned_pile.parquet")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--batch-size", type=int, default=1)
    args = parser.parse_args()

    try:
        from datasets import load_dataset
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        raise SystemExit(
            "Evaluation dependencies are missing. Run the project setup first."
        ) from exc

    model_path = Path(args.model)
    if not model_path.exists():
        raise SystemExit(f"Model directory does not exist: {model_path}")
    dataset = load_dataset("parquet", data_files=args.data, split="train")
    split = dataset.train_test_split(test_size=0.1, seed=42)["test"]
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def tokenize(batch: dict[str, list[str]]) -> dict[str, list[list[int]]]:
        return tokenizer(batch["text"], truncation=True, max_length=args.max_length)

    tokenized = split.map(tokenize, batched=True, remove_columns=split.column_names)
    model = AutoModelForCausalLM.from_pretrained(model_path)
    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    training_args = TrainingArguments(
        output_dir="evaluation_output",
        per_device_eval_batch_size=args.batch_size,
        report_to=[],
        fp16=False,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        eval_dataset=tokenized,
        data_collator=collator,
    )
    metrics = trainer.evaluate()
    loss = metrics.get("eval_loss")
    if loss is None:
        raise SystemExit("The evaluator did not return eval_loss.")
    perplexity = math.exp(loss) if loss < 20 else float("inf")
    print(f"Evaluation loss: {loss:.4f}")
    print(f"Perplexity: {perplexity:.4f}")


if __name__ == "__main__":
    main()