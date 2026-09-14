# Phase C — SFT Aali's own brain (relaunch after power-off)

- owner: buffy (this PC, Freebuff night session)
- status: in-progress (run 5 in flight: masking + row-shape + generation guards)
- started: 2026-09-14 ~00:40 local

## Why
The owner accidentally powered the PC off (~2026-09-13 23:31). This killed:
the promoted-brain server (:20129), the Aali API (:5055), the Phase C chain
waiter (was waiting on the GPU since 21:48), the night caretaker, and the v5
exam-breakdown watcher. The v5 graduation itself was already DONE
(checkpoint-3873 PROMOTE, verdict written 18:19), Phase B is complete, and the
bootstrapped Phase C state (D:/hwk-models/aali-sft-4k, step 0) survived.

## Night 2 (2026-09-14 ~07:00-08:30) — ROOT CAUSE found + fixed
The first Phase C SFT "passed" (loss 0.0009) but the served brain output an
infinite `::::::` attractor. `_probe_phase_c_brain.py` showed SFT and RAW-B
both degenerate, which pointed past serving-format to the trainer itself:

1. **Identity collapse (fatal)**: `SftDataset.batches` built `targets ==
   inputs` (no next-token shift) while the model computes loss over
   full-length logits and ignores only -100 → the SFT silently trained a
   perfect token-copy task. eval_loss 0.0009 on UNSEEN rows was the bug
   waving, not health. Fixed in train_scratch.py (inputs seq[:-1], targets
   seq[1:], -100 pad targets); pinned by tests/test_sft_dataset_shift.py.
2. **Serving-format gaps (two)**: training rows must match
   `_local_model_loop`'s transcript EXACTLY — System line first, THEN
   "Available tools:", then turns, then the instruction + "Assistant:"
   anchor. Fixed via `_sft_record_text` + SCRATCH_SYSTEM_LINE; pinned by
   test_sft_record_text_ordering_matches_serving_transcript.
3. Also fixed: phase_c_after_v4.py smoke generation now uses the full
   serve-shape prompt (the bare-prompt smoke misled the first review);
   two night-session test bugs (context too small after the render grew;
   batches() shuffles so rows must be matched by content, not position).

## Verification
- Suite: 407 passed (pytest, .venv).
- Broken model archived at D:/hwk-models/aali-sft-4k.broken-shift-bug.
- Re-bootstrapped from Phase B (weights 109.6M, fresh optimizer, step=0).
- Retrain launched 07:36 local detached (phase_c_chain.log): loss 0.81 and
  falling at step 120 (vs 0.0009 copy-loss in the broken run), eval_loss
  0.335 declining, ~45k tok/s, ETA ~30 min.

## Run 5 (2026-09-14 evening) — the salad run root-caused: FOUR defects
Run 4 (this morning's retrain, eval_loss 0.77) produced bilingual word salad
in the smoke. Three more trainer defects found past the shift bug:

4. **Diluted gradient**: loss ran over the WHOLE row, so the constant
   boilerplate (System line, tool list, instruction, role lines) dominated
   the gradient — format memorized, answers weak. SftDataset now carries
   completion-only loss masks (-100 on prompt targets; the row's EOS stays
   unmasked so the model learns to STOP; mask_prompt=False = legacy escape).
5. **Row shape**: rows are PROMPT (System, tools, leading turns, instruction,
   "Assistant:" anchor) + " " + COMPLETION (the final assistant turn), EOS
   after the answer (_sft_prompt_text / _sft_completion_text /
   _sft_record_text; _sft_loss_mask pins the (row, completion) contract).
6. **Generation guards**: hwk_model/generation.py — CTRL-style repetition
   penalty 1.15 (generated tokens only, applied before argmax so greedy
   loops break too) + <unk> banned from sampling (⁇ never spoken;
   ban_unk=False restores).

Verification: suite 423 green (test_sft_dataset_shift.py rewritten ~20
locks, new test_generation_guards.py). Run-4 model archived as
D:/hwk-models/aali-sft-4k.broken-run4-salad; re-bootstrapped from Phase B
(context-4k, 109.6M weights, fresh optimizer, step=0); chain relaunched
detached 17:29 local (phase_c_chain.log pid 61356). Early curve: loss ~2.1
at step 150 and falling, eval 0.98 declining, ~45.7k tok/s, ETA ~30 min.

## Run 5 OUTCOME (18:10-18:18) — progress, NOT deployable
2800 steps / 38 min, exit 0, eval_loss 0.77 (whole-run masked task — not
comparable to the 0.0009 identity-collapse run). Smoke verdict:
- AR prompt: REAL Arabic sentence now (run 4 was pseudo-Quran salad) but
  drifts and stops early ("باختصار ما إذا كان الطلب على الملف if...»").
- EN prompt: still loops — "emojis:" ×14 EVEN WITH the repetition penalty;
  language mixing persists.
- No ⁇ unk glyphs (ban worked).
Verdict: DO NOT replace model/scratch/final.pt yet. A 110M brain at 4
epochs/91M tokens is still weak on the 1.5B-targeted mix. Next levers (human
call): more SFT epochs on a distilled/cleaner small-model mix, stronger loop
suppression (no-repeat-ngram window), lower serving temperature with the
guards, or accept the scratch brain needs a bigger Phase C budget.

## Remaining plan (rest of the window)
1. When training lands: re-probe with _probe_phase_c_brain.py (serve shape).
2. Human decision: replace file-agent/model/scratch/final.pt (copy final.pt
   + config.json) — Aali then codes and chats from his OWN brain.
3. Commit the fixes; keep the API on AALI_OWN_MODEL=0 until scratch lands.
