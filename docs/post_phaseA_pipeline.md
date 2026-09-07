# Post-Phase-A pipeline — teacher baseline, re-SFT, gated promotion

*Built 2026-09-06. Runs automatically since 18:52 UTC (watching for the GPU
to free up); everything here also works manually.*

## What fires, in order (scripts/soup_pipeline.py)

1. **Wait** — rechecks every 5 min: no python-family compute process on the
   GPU + `training.log` idle ≥15 min. Browsers hold CUDA contexts too, so
   only *python-family* processes block (the Phase A trainer is the one that
   matters). Never touches a live run.
2. **Serve** — Soup serves Qwen2.5-1.5B-Instruct on 127.0.0.1:20129
   (up to 10 min for the first download).
3. **Baseline exam** — the 24-case exam (memory + security +
   anti-hallucination + tools) via `scripts/soup_exam.py` →
   `D:/hwk-data/soup/soup_exam_report_baseline.json`.
4. **Re-SFT** — `soup train` (QLoRA 4-bit, ≤3 epochs) on
   `D:/hwk-data/soup/sft_v2.jsonl` — see "The sft_v2 dataset" below.
5. **Tuned exam** — same 24 cases against base+adapter →
   `soup_exam_report_tuned.json`.
6. **Verdict** → `D:/hwk-data/soup/resft_pipeline_report.md`:
   promotion only if the tuned model strictly beats the baseline
   (tie = NO-GO, more data first).

Every stage is logged to `D:/hwk-data/soup_pipeline.log`; the last stage's
raw output lands in `D:/hwk-data/soup_pipeline_last_stage.txt`. Any stage
failure stops the pipeline — no blind retries on a live GPU.

## The sft_v2 dataset (scripts/build_aali_sft_v2.py)

Built 2026-09-06, **4,086 records**, dedup + exam-leak gated:

| Source | Records | Notes |
|---|---|---|
| sft_mix (Alpaca core) | 3,998 | capped from 12,090, evenly spread |
| tool_calling_sft | 62 | the exam-shaped tool behaviors |
| mentor sessions | 12 | **failures ×3** — the owner's "learn from my failures" |
| live conversation log | 1 | grows with every real chat |
| generated memory/security/verification | 17 | new behaviors (before leak gate) |

Gates that proved themselves on the first build: 30 duplicate pairs removed,
**4 generated episodes dropped as exam leaks** (their prompts matched exam
prompts — the gate prevents score inflation).

Honest gap: Arabic share ≈1.4%. The ≥1,000-Arabic-records drive is still
pending and is the top data priority for the next session.

## Manual operation

```bash
# pipeline status
tail -5 D:/hwk-data/soup_pipeline.log
# rebuild the dataset anytime (after new mentor/chats arrive)
.venv/Scripts/python.exe scripts/build_aali_sft_v2.py
# run any stage by hand
.venv/Scripts/python.exe scripts/soup_pipeline.py --no-wait
```

Kill the watcher any time: close its window or
`Get-CimInstance Win32_Process -Filter "Name like 'python%'"` →
find `soup_pipeline` → `Stop-Process -Id <pid>`.
