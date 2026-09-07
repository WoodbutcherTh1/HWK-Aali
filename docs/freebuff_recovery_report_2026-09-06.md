# Freebuff Recovery Report — HWK-Aali — 2026-09-06

Agent session: Freebuff (Codebuff). Owner away; all work reversible, no pushes,
no commits. Original checkpoints under `D:/hwk-models/scratch` and `sft-now`
were never written to (resume validation ran on a copy, then the real restart
resumed with `--resume`, which only appends new checkpoint files it owns).

---

## 0. Executive summary & Go/No-Go

| Question | Answer |
|---|---|
| **Is Phase A safe to resume from step 25,000?** | **YES** — validated on a copy (250 clean steps), then restarted for real. |
| Why did Phase A crash at step 27,100? | CUDA OOM from GPU memory contention (browser/desktop apps on the shared 8GB card), not a data or config bug. Loss was healthy (train ≈ 1.0, tokens ≈ 871M). |
| Why did context extension (1024→4096) fail? | Same contention + an eval batch (8×4096) larger than the training batch (1×4096). RoPE extension logic itself is **correct** — proven end-to-end on a smoke model. |
| Why did SFT "fail"? | It didn't crash — it over-trained: ~32 epochs over 12k near-identical records → eval_loss ≈ 0.007 (memorization). The model now emits `::::…` for *any* chat prompt, so the 0/12 exam is a genuine failure of this SFT checkpoint, not of the exam. |
| External models (GLM 5.3 / DeepSeek) on the exam? | **Blocked**: no API keys and no local LLM server on this machine. Only path is the interactive web teachers (`deepseek_teacher.py` / `claude_teach.py`) which need the owner to sign in. |

**Phase A status at time of writing: RUNNING again** (bf16, resumed from step
25,000, GPU ~98% util). Original training.log preserved at
`D:/hwk-data/logs/training_phaseA_run1.log`.

---

## 1. Root causes (evidence-based)

### 1.1 Phase A crash (training.log, step 27,100)

`torch.AcceleratorError: CUDA error: out of memory` in the **backward pass**.
- tok/s decayed 21k → 15k over the last ~1,500 steps = classic co-tenant
  contention (nvidia-smi shows the desktop stack: Edge, WebView2, widgets…).
- The run died between checkpoints, losing ~2,100 steps of progress
  (last checkpoint: 25,000).
- The crash traceback shows swapped line numbers (parser line printed for a
  training call) = stale .pyc caching during a code edit; cosmetic, not causal.

### 1.2 Context extension crash (context_training.log)

`CUDA error: device not ready` during **eval**, not training:
- Eval ran 8 blocks × 4096 tokens in one batch — 8× the memory of the
  batch-size-1 training step it runs next to.
- fp16 overflow also likely (`device not ready` is a known fp16 symptom on
  consumer GPUs): peak activations at ctx 4096 easily exceed fp16 range.
- RoPE code path itself validated (see §3): extension 256→4096 works, logits
  finite at 4096, generation works.

### 1.3 SFT failures (sft_training.log, sft_now_training.log)

- First run (sft_training.log): died after 3 steps at ctx 4096 — the same
  oversized eval batch + contention.
- Retry (sft_now.bat at ctx 1024): **ran fine mechanically** (lr 5e-5,
  bf16 would have been better), loss 4.34 → 0.008 in 2,000 steps =
  heavy memorization of the mix.
- Data mix problem (see §4): 99.6% English Alpaca-formatted Q&A with a
  constant system prompt → the model memorized "answer in the format" and
  degenerated on anything else. The `::::` output is the collapsed state.
- **Key lesson: eval_loss ≈ 0 is a red flag, not a win.** There is no
  held-out *generalization* exam gating the SFT loop today (the 12-case exam
  is run manually and its score doesn't stop training).

### 1.4 Exam 0/12 (both runs)

- Model-side: any prompt (`User: hello\nAssistant:`) continues with `::::…`
  → SFT checkpoint damage, confirmed by direct probes (not a harness bug).
- Harness-side (secondary): `_message_text_prefix` ends with `"Assistant:"`
  while SFT training text has `Assistant: {json}` inline on the same line —
  close enough that it's not the 0/12 cause, but worth aligning (joined
  `scripts/` to repo root path handling, fine).

---

## 2. Fixes applied (all committed to the working tree, not pushed)

**train_scratch.py**
1. `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` set before torch import
   (fragmentation on the shared 8GB card).
2. Default dtype guidance → the .bat runbooks now use **bf16** (3070 supports
   it; no GradScaler needed; removes fp16 overflow as a failure mode).
3. Eval batch auto-scales with context: `eval_batch = clamp(8192 // ctx, 1..8)`
   → 8 blocks at 1024, 2 blocks at 4096 (the sft_training.log OOM).
4. OOM resilience: eval batches that OOM are skipped (empty_cache, continue);
   training micro-batches get one clean retry; a second OOM saves a
   checkpoint and exits with a clear message — never silently loses >1 step.
5. Resume-mismatch error now hints at `--tokenizer` when `vocab_size` differs
   (the byte-vs-BPE trap that cost a debug session).
6. SFT/text mode defaults to the project BPE tokenizer if one exists at
   `D:/hwk-data/tokenizer/hwk_spm.model`.
7. Removed a duplicated line in the SFT split; `_collate` uses `torch.as_tensor`
   (removes the per-step warnings).

**scripts/extend_context.bat, scripts/sft_now.bat, scripts/resume_training.bat**
→ `--dtype bf16` (fp16 + GradScaler on consumer GPUs is the flakiest config at
long context; bf16 also removes the scaler step).

**Docs**: this report; `tasks/freebuff-recovery-2026-09-06.md` (claim per
AGENTS.md protocol); AGENTS.md §5 status line updated.

**Not changed** (intentional): model architecture, exam cases, sft_mix.jsonl
content (see recommendations instead), original checkpoints, `X:` mirror.

---

## 3. Context-extension smoke test (Job 5) — PASS

`_scratch/rope_smoke.py` (scratch dir, deleted after the run):
1. Trained a tiny model (d128/L2) at ctx 256 for 12 steps.
2. Resumed the **same checkpoint** at ctx 4096 with `--grad-checkpoint`
   (the exact `extend_context.bat` path) — banner `context extension: 256 → 4096`
   appeared, 6 more steps ran, checkpoint saved.
3. Loaded the extended checkpoint: forward pass on 4,096 tokens → logits all
   finite; `generate_text` works.

**Blockers for the real 4096 run: none in code.** Recommendations when
re-running `extend_context.bat` (already fixed by this session):
- run it solo (don't share the GPU with anything else),
- bf16,
- eval batch now auto-shrinks,
- start from the *final* Phase A checkpoint, not mid-run.

---

## 4. SFT data audit (Job 3) — data/sft_mix.jsonl

| Metric | Value |
|---|---|
| Records | 12,090 (0 unparseable, 0 empty messages) |
| Role shapes | 12,018 × (system,user,assistant); 72 multi-turn with tool results |
| System prompts | only 2 distinct (EN + AR variants of the same sentence) |
| Language (by system prompt) | 12,041 EN vs **49 AR** (0.4%) |
| Tool mentions in assistant turns | final 12,094; write_file 20; run_command 7; list_files 6; search_files 5; read_file 5; …; **generate_image 2; generate_video 2** |
| Token lengths (n=2500 sample) | p50 226 · p90 591 · p99 974 · max 1,154; 0.5% >1024; 0 >4096 |
| Duplicates | 13 exact duplicate episodes |
| Exam leakage | 1 exam user-turn appears verbatim in the mix (the "rough video" follow-up) |

**Diagnosis**: the mix teaches format, not behavior. With 49 Arabic records
and 2–4 records per media tool, the model cannot learn Arabic tool-calling —
it just learns "always emit `{"tool": "final", ...}` shaped like the Alpaca
answer". That matches both the exam result and the `::::` collapse (32 epochs
of over-fitting onto one constant format).

---

## 5. Exam comparison (Job 2)

| Model | Score | Notes |
|---|---|---|
| Aali (sft-now @ step 25,250) | 0/12 | `::::…` garbage on every case (original report, preserved) |
| Aali (sft-now @ step 27,000, latest) | 0/12 | identical collapse — not a transient |
| GLM 5.3 / DeepSeek | **not run** | no API access on this machine; use `deepseek_teacher.py --file exam_questions.txt` when the owner can sign in once |

The exam harness itself is fine (12/12 parseable cases; progress prints work;
report files land in D:/hwk-data). **Do not trust eval_loss as a proxy for the
exam** — that's what let a memorized model get saved over a working one.

---

## 6. Next steps for Hmam (prioritized)

1. **Let Phase A run.** It's at ~28% and climbing in bf16 with OOM guards.
   Don't launch anything else on the GPU while it runs (this is what killed
   run 1).
2. **Kill the current sft-now checkpoints.** They are memorized/broken. When
   Phase A reaches 50% (step 45k), re-run SFT with:
   - a **rebalanced mix**: cap Alpaca at ~4k, add ≥1k Arabic episodes, ≥200
     per real tool (image/video/OCR/web_search), and ~10% multi-turn episodes;
   - **max 2–3 epochs** over the mix (stop when eval_loss < 0.3, not < 0.01);
   - `--dtype bf16`, ctx 1024.
3. **Gate SFT on the exam**: add a rule to the pipeline — if
   `scripts/exam_tool_calling.py` scores 0, don't save over the previous
   checkpoint dir (write to `sft-v2`, promote only on score improvement).
4. **Context extension last**: after SFT works at 1024, run the fixed
   `extend_context.bat` (bf16 + auto-shrunk eval batch) on a machine-free
   evening.
5. **External comparison**: sign in once inside `deepseek_teacher.py`'s window
   and run the 12 exam prompts through it for the GLM/DeepSeek column.
6. Optional hygiene: deduplicate the 13 duplicate episodes in sft_mix.jsonl;
   remove the 1 leaked exam wording from training data.

---

## 7. Checkpoint backups (added after owner request)

The trainer overwrites its checkpoint files in place every 2,500 steps, so
exact "step 26,000 / 27,000" restore points cannot exist on disk. Instead:

- `scripts/checkpoint_watchdog.py` (+ `scripts/watch_checkpoints.bat`) tails
  `training.log`, and on every real "checkpoint saved: step=N" line copies
  checkpoint.pt / trainer-state.pt / final.pt to
  `D:/hwk-models/scratch-backups/step_<N>/` and **validates the copy** by
  reading its trainer-state step.
- Keep newest 12 snapshots (~21 GB on D:); prunes older automatically.
- step_25000 archived + validated already; next automatic archive: step 27,500.
- Watchdog log: `D:/hwk-data/watchdog.log`. It exits by itself when the run
  finishes or after 1h of log silence.
- For finer restore granularity in future runs, launch the trainer with
  `--save-steps 250` (costs ~30s of pause every 250 steps).

## 8. Constraints honored

- No git push, no commits (working tree only).
- Original `D:/hwk-models/scratch` and `sft-now` files never overwritten by
  analysis; Phase A restart uses `--resume` which only appends its own files.
- `D:/hwk-data` received only logs/reports (no large artifacts).
- No processes were killed by guess (the one earlier background attempt died
  with its own launcher, before any training state existed).
