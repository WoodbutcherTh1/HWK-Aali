"""Train the local assistant model on cleaned Pile text."""

from __future__ import annotations

import argparse
import csv
import inspect
from pathlib import Path


class CsvTrainingLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fieldnames = ["step", "loss", "grad_norm", "learning_rate", "epoch"]

    def write(self, values: dict[str, object]) -> None:
        if not values:
            return
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=self.fieldnames,
                extrasaction="ignore",
            )
            if self.path.stat().st_size == 0:
                writer.writeheader()
            writer.writerow(values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the local HWK model.")
    parser.add_argument("--data", default="processed_data/cleaned_pile.parquet")
    parser.add_argument("--model-name", default="distilgpt2")
    parser.add_argument("--output-dir", default="model")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--save-steps", type=int, default=500)
    parser.add_argument("--use-4bit", action="store_true")
    args = parser.parse_args()

    try:
        from datasets import load_dataset
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainerCallback,
            TrainingArguments,
        )
    except ImportError as exc:
        raise SystemExit(
            "Training dependencies are missing. Run ./setup_replit.sh first."
        ) from exc

    import torch

    dataset = load_dataset("parquet", data_files=args.data, split="train")
    split = dataset.train_test_split(test_size=0.1, seed=42)
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def tokenize(batch: dict[str, list[str]]) -> dict[str, list[list[int]]]:
        return tokenizer(batch["text"], truncation=True, max_length=args.max_length)

    tokenized = split.map(tokenize, batched=True, remove_columns=split["train"].column_names)
    model_kwargs: dict[str, object] = {}
    if args.use_4bit:
        try:
            import torch
            from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
            from transformers import BitsAndBytesConfig
        except ImportError as exc:
            raise SystemExit(
                "4-bit training needs torch, bitsandbytes, and peft."
            ) from exc
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
        )
        model_kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(args.model_name, **model_kwargs)
        model = prepare_model_for_kbit_training(model)
        model = get_peft_model(
            model,
            LoraConfig(
                r=16,
                lora_alpha=32,
                lora_dropout=0.05,
                target_modules=["c_attn", "c_proj"],
                task_type="CAUSAL_LM",
            ),
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(args.model_name)

    log_path = Path("training_log.csv")
    logger = CsvTrainingLogger(log_path)

    class LogCallback(TrainerCallback):
        def on_log(self, args: object, state: object, control: object, logs: dict | None = None, **_: object) -> None:
            if logs:
                logger.write({"step": getattr(state, "global_step", ""), **logs})

    training_kwargs: dict[str, object] = {
        "output_dir": args.output_dir,
        "num_train_epochs": args.epochs,
        "per_device_train_batch_size": args.batch_size,
        "per_device_eval_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation,
        "save_steps": args.save_steps,
        "logging_steps": 10,
        "save_total_limit": 2,
        "fp16": torch.cuda.is_available(),
        "report_to": [],
        "logging_dir": "training_logs",
        "eval_accumulation_steps": 1,
    }
    supported = inspect.signature(TrainingArguments).parameters
    strategy_key = "eval_strategy" if "eval_strategy" in supported else "evaluation_strategy"
    training_kwargs[strategy_key] = "steps"
    training_kwargs["eval_steps"] = args.save_steps
    training_args = TrainingArguments(**training_kwargs)
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["test"],
        tokenizer=tokenizer,
        data_collator=DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False),
        callbacks=[LogCallback()],
    )
    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"Training complete. Model saved to {args.output_dir}/")


if __name__ == "__main__":
    main()