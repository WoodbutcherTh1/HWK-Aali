# Legacy scripts (deprecated)

These scripts belonged to the earlier hosted/pretrained-model direction
(`transformers.pipeline`, DistilGPT2, Qwen2.5 fine-tuning). The project is now
**from-scratch only**, so they are retired and kept here for reference only:

- `train.py` — old Transformers-based training (DistilGPT2 path).
- `train_agent.py` — old Qwen instruction fine-tuning (requires a pretrained
  base model; conflicts with the from-scratch requirement).
- `evaluate.py` — old Transformers-based evaluator.

Use instead:

- `train_scratch.py` — from-scratch pretraining / SFT (random weights).
- `evaluate_scratch.py` — loss/perplexity for scratch checkpoints.
- `probe_model.py` — sample generations (Arabic/English/tools/domains).

Do not re-enable these without explicitly revisiting the from-scratch
decision.