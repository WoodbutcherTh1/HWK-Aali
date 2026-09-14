# Phase C — SFT Aali's own brain (relaunch after power-off)

- owner: buffy (this PC, Freebuff night session)
- status: in-progress (retrain in flight with the shift fix)
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

## Remaining plan (rest of the window)
1. When training lands: re-probe with _probe_phase_c_brain.py (serve shape).
2. Human decision: replace file-agent/model/scratch/final.pt (copy final.pt
   + config.json) — Aali then codes and chats from his OWN brain.
3. Commit the fixes; keep the API on AALI_OWN_MODEL=0 until scratch lands.
