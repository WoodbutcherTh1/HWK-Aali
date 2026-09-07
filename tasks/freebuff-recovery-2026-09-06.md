# Freebuff recovery session — 2026-09-06

- owner: freebuff (Codebuff agent)
- status: done
- started: 2026-09-06 18:50
- finished: 2026-09-06 19:35

## Outcome

All jobs complete or explicitly blocked (see docs/freebuff_recovery_report_2026-09-06.md).
Phase A is RUNNING again from step 25,000 (bf16, OOM-guarded trainer).
sft-now is condemned (memorized, 0/12 exam); re-SFT plan in the report.
Leave this claim file for the owner to read; delete after review.

## Scope

Jobs from the handoff (analysis + recovery):

1. Diagnose Phase A crash (step 27,100), context-extension failure, SFT failures.
2. SFT data quality audit (`data/sft_mix.jsonl`).
3. Context-extension (RoPE 4096) feasibility smoke test on a small model.
4. Validate Phase A resume from step 25,000 **on a copy**, then restart Phase A.
5. Re-run the tool-calling exam on the latest `sft-now` checkpoint.
6. Fix the code/config bugs found (train_scratch.py eval-batch OOM, dtype,
   allocator config, .bat runbooks).

## Rules honored

- No git push, no commits unless asked.
- Original checkpoints under D:/hwk-models/scratch and sft-now are never
  written to; resume validation runs on a copy (D:/hwk-models/resume-test).
- D:/hwk-data stays clean (logs only).
- GPU shared politely: exam/validation runs happen before Phase A restart.
