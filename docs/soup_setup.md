# Soup integration for HWK-Aali — 2026-09-06

Soup (https://github.com/MakazhanAlpamys/Soup) is installed as Aali's local
teacher + comparison model, in its own environment (nothing touched the
training venv, per AGENTS.md).

## Layout

| Path | What |
|---|---|
| `D:/hwk-tools/soup-venv/` | Soup + torch 2.14 + transformers 5.16 + trl 0.29 + peft 0.20 (uv env, Python 3.12) |
| `%USERPROFILE%/.local/bin/uv.exe` | uv (package manager used to create the env) |
| `soup.yaml` (repo root) | HWK config: Qwen2.5-1.5B-Instruct base, 4-bit QLoRA, our SFT mix |
| `scripts/soup_export_sft.py` | Converts `data/sft_mix.jsonl` → `D:/hwk-data/soup/sft_alpaca.jsonl` + exports the 12 exam cases |
| `scripts/soup_exam.py` | Grades a Soup-served model on the same 12-case exam Aali takes |
| `D:/hwk-data/soup/` | Exported data + exam prompts + report (data stays off the repo) |

## Use case 1 — local teacher for Arabic SFT data

The re-SFT rebalance (recovery report §6) needs ≥1,000 Arabic episodes; today
there are 49. Instead of the manual web teachers, generate them locally:

```bash
# 1. Pause Phase A (close the training window) — the GPU is needed.
# 2. Fine-tune the small teacher on the current mix (~30-60 min on the 3070):
D:/hwk-tools/soup-venv/Scripts/soup.exe train --config soup.yaml
# 3. Chat with it to brainstorm+draft Arabic episodes, or batch-generate:
D:/hwk-tools/soup-venv/Scripts/soup.exe chat --config soup.yaml
D:/hwk-tools/soup-venv/Scripts/soup.exe infer --config soup.yaml --input prompts.jsonl
# 4. REVIEW every episode before it enters data/ — the teacher drafts,
#    the owner (or the self-verification rules) approve. Never train on
#    unchecked generations.
```

## Use case 2 — comparison model on Aali's exam

```bash
D:/hwk-tools/soup-venv/Scripts/soup.exe serve --model Qwen/Qwen2.5-1.5B-Instruct --port 20129
.venv/Scripts/python.exe scripts/soup_exam.py --base-url http://127.0.0.1:20129/v1
```
Same prompts, same grading as `scripts/exam_tool_calling.py` → the score is
directly comparable with Aali's. Report lands in `D:/hwk-data/soup_exam_report.json`.

## Use case 3 — profile before any training

```bash
D:/hwk-tools/soup-venv/Scripts/soup.exe profile --config soup.yaml
```
If the plan exceeds ~7 GB VRAM, lower `max_length` or `lora.r` in soup.yaml.
Never run train/serve while Phase A pretraining is using the GPU — pause it
first (close the training window; it resumes cleanly).

## Validation already done (CPU-only)

- `soup doctor`: all required checks OK.
- `soup data validate D:/hwk-data/soup/sft_alpaca.jsonl --format alpaca`:
  12,090/12,090 rows valid; **13 duplicate rows** — independently confirming
  the recovery-report audit number.
- Exam exporter + grader self-test pass.

## Not done yet (needs the GPU / owner decision)

- The ~3 GB base-model download and any train/serve run (Phase A owns the GPU).
- Swapping the base to a 7B/8B teacher after Phase A completes.
