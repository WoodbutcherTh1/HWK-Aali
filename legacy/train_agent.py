"""Fine-tune a small local model to emit file-agent tool calls."""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path


def load_records(path: str | Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}") from exc
            if not isinstance(record, dict) or not isinstance(record.get("messages"), list):
                raise ValueError(f"Line {line_number} must contain a messages list")
            records.append(record)
    if not records:
        raise ValueError("The training dataset is empty")
    return records


def format_record(record: dict[str, object]) -> str:
    messages = record["messages"]
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Every message must be an object")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("Every message needs string role and content")
        parts.append(f"{role.capitalize()}: {content}")
    return "\n".join(parts) + "\nAssistant:"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune the Arabic file-agent model.")
    parser.add_argument("--data", default="data/agent_instructions.jsonl")
    parser.add_argument("--model-name", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--output-dir", default="model/agent")
    parser.add_argument("--max-length", type=int, default=768)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--save-steps", type=int, default=500)
    parser.add_argument("--use-4bit", action="store_true")
    args = parser.parse_args()

    try:
        import torch
        from datasets import Dataset
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

    records = load_records(args.data)
    dataset = Dataset.from_list([{"text": format_record(record)} for record in records])
    split = dataset.train_test_split(test_size=0.1, seed=42)
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def tokenize(batch: dict[str, list[str]]) -> dict[str, list[list[int]]]:
        return tokenizer(batch["text"], truncation=True, max_length=args.max_length)

    tokenized = split.map(tokenize, batched=True, remove_columns=["text"])
    if args.use_4bit:
        try:
            from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
            from transformers import BitsAndBytesConfig
        except ImportError as exc:
            raise SystemExit(
                "4-bit training needs bitsandbytes and peft."
            ) from exc
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
        )
        model = AutoModelForCausalLM.from_pretrained(
            args.model_name,
            quantization_config=quantization,
            device_map="auto",
        )
        model = prepare_model_for_kbit_training(model)
        linear_leaf_names = {
            name.rsplit(".", 1)[-1]
            for name, module in model.named_modules()
            if isinstance(module, torch.nn.Linear)
        }
        preferred_targets = [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj", "c_attn", "c_proj",
        ]
        targets = [name for name in preferred_targets if name in linear_leaf_names]
        if not targets:
            raise SystemExit("Could not find compatible LoRA target modules in the model.")
        model = get_peft_model(
            model,
            LoraConfig(
                r=16,
                lora_alpha=32,
                lora_dropout=0.05,
                target_modules=targets,
                task_type="CAUSAL_LM",
            ),
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(args.model_name)

    training_kwargs: dict[str, object] = {
        "output_dir": args.output_dir,
        "num_train_epochs": args.epochs,
        "per_device_train_batch_size": args.batch_size,
        "per_device_eval_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation,
        "gradient_checkpointing": True,
        "save_steps": args.save_steps,
        "logging_steps": 1,
        "save_total_limit": 2,
        "fp16": torch.cuda.is_available(),
        "report_to": [],
        "remove_unused_columns": False,
    }
    supported = inspect.signature(TrainingArguments).parameters
    strategy_key = "eval_strategy" if "eval_strategy" in supported else "evaluation_strategy"
    training_kwargs[strategy_key] = "steps"
    training_kwargs["eval_steps"] = args.save_steps
    training_args = TrainingArguments(**training_kwargs)
    trainer_kwargs: dict[str, object] = {
        "model": model,
        "args": training_args,
        "train_dataset": tokenized["train"],
        "eval_dataset": tokenized["test"],
        "data_collator": DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False),
    }
    trainer_parameters = inspect.signature(Trainer).parameters
    if "processing_class" in trainer_parameters:
        trainer_kwargs["processing_class"] = tokenizer
    else:
        trainer_kwargs["tokenizer"] = tokenizer
    trainer = Trainer(**trainer_kwargs)

    class LossLogger(TrainerCallback):
        def on_log(self, args: object, state: object, control: object, logs: dict | None = None, **_: object) -> None:
            if logs:
                output = Path("agent_training_log.jsonl")
                with output.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"step": state.global_step, **logs}, ensure_ascii=False) + "\n")

    trainer.add_callback(LossLogger())
    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"Agent model saved to {args.output_dir}/")


if __name__ == "__main__":
    main()