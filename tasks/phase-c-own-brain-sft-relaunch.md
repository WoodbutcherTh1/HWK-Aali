# Phase C — SFT Aali's own brain (relaunch after power-off)

- owner: buffy (this PC, Freebuff session)
- status: done (2026-09-26 owner decision: KEEP checkpoint-3873 as the served brain;
  scratch brain = research. Continuation lives in tasks/distill-teacher-scratch.md)
- started: 2026-09-14 ~00:40 local
- closed: 2026-09-26

## Why
The owner accidentally powered the PC off (~2026-09-13 23:31). This killed:
the promoted-brain server (:20129), the Aali API (:5055), the Phase C chain
waiter (was waiting on the GPU since 21:48), the night caretaker, and the v5
exam-breakdown watcher. The v5 graduation itself was already DONE
(checkpoint-3873 PROMOTE, verdict written 18:19), Phase B is complete, and the
bootstrapped Phase C state (D:/hwk-models/aali-sft-4k, step 0) survived.

## RELAUNCH (2026-09-26 ~07:15, owner green-light)

Phase D complete (stage 5: eval 1.94, ctx 8192, weights at
D:/hwk-models/phase-d-8k). Owner GO'd the Phase C SFT retry on the new base:

1. Pre-flight verified: trainer default dropout 0.1 == phase-d-8k config
   (resume config check passes; context 8192->4096 is RoPE-exempt);
   aali-sft-4k held the STALE context-4k bootstrap -> archived to
   aali-sft-4k.context4k-bootstrap and re-bootstrapped from phase-d-8k;
   sft_v2.jsonl (5,472 rows, toolbelt mix) unchanged;
   phase_c_after_v4 envs: AALI_PHASE_C_STEPS=2500 AALI_PHASE_C_LR=2e-5
   (sft_small lesson: 2500 ~ 6 epochs; run 6 concluded capacity was the
   wall, and phase-d-8k is a much stronger base).
2. GPU freed: brain :20129 killed by exact netstat PID (rule-compliant);
   brain watchdog left running by design — card_is_safe makes it WAIT
   while the trainer holds the card (never fights a trainer).
3. Chain launched detached: launch_detached.py --log phase_c_chain
   (waits for GPU free via the shared gate, then train_scratch --resume,
   then serve-shape smoke into phase_c_chain.log).
4. NO automatic promotion: model/scratch/final.pt replaced only on the
   owner's decision after a good smoke (same gate as before).

## RELAUNCH OUTCOME (2026-09-26 08:26) — NOT deployable

Run 7 completed clean (exit 0, 2500 steps, 81.9M tokens, ~38 min,
tok/s ~36-47k): loss ~1.9, eval 2.156 -> 2.049. Smoke (serve-shape, CPU):
- BOTH answers are pseudo-Quranic salad (``«اب بَيْتَ الأَرْضَ...»`` style)
  with diacritics — the phase-d-8k base's own pretraining attractor.
- ZERO tool-protocol JSON in either answer (worse than run 6's shape).
Verdict: DO NOT PROMOTE. model/scratch/final.pt untouched.

**Diagnosis — the data is the wall now, not the trainer and not only
capacity**: sft_v2.jsonl (5,472 rows) was built to teach a Qwen-1.5B
ADAPTER the protocol. Every row embeds the SOUP serving template — the
Qwen chat template + English protocol sentence ("Reply with either a
natural-language answer or exactly one JSON object...") + LOTS of JSON.
Phase C serves the SCRATCH transcript instead (System line -> tools ->
turns -> "Assistant:"), so the formats never matched: the model learns
soup-shaped rows, then is probed in a shape it never saw. Fix before any
run 8: rebuild the SFT mix in the SCRATCH row shape (bootstrap_sft_state
docstring + _sft_record_text hold the contract; hooks exist in
scripts/build_aali_sft_v2.py's builder family).

Services restored after the run: brain :20129 (checkpoint-3873, verified
/v1/models) + API :5055 healthy + brain watchdog relaunched (pid 75328).
Stale bootstrap archived at D:/hwk-models/aali-sft-4k.context4k-bootstrap;
run-7 weights at D:/hwk-models/aali-sft-4k.

## RUN 8 (2026-09-26 ~09:42, Buffy) — scratch-shaped mix, launched

Owner GO. The run-7 fix (rebuild in SCRATCH row shape) implemented:

1. `scripts/build_aali_sft_scratch.py` (NEW, tests/test_sft_scratch_builder.py
   17): converts sft_v2.jsonl -> sft_scratch.jsonl in the SCRATCH contract —
   DROPS the soup protocol system message from every row (the trainer
   prepends SCRATCH_SYSTEM_LINE; the record system line would render as a
   SECOND System turn), UNWRAPS {"tool":"final","content":X} to plain X at
   every assistant position (3,520 unwrapped — scratch serves plain text;
   the soup envelope rendered raw would reach users), REPAIRS invented tool
   names via tool_guard ALIASES + one conservative fuzzy match and DROPS
   rows with unresolvable names (parse-and-redump, never string-replace),
   gates answer<=200 / total<=900 estimated tokens (v2 estimator) and the
   ends-with-assistant structure. Output: 5,472 -> 5,472 rows, 0 drops,
   0 name repairs (v2's registry gate had already cleaned names),
   completions 66% JSON -> 2% JSON (5,361 plain + 111 real tool calls),
   arabic share 0.356 preserved. Report: sft_scratch.report.json.
   Pinned by tests that render converted records through
   train_scratch._sft_record_text and assert the EXACT serving transcript
   shape (System line -> tools -> turns -> instruction -> answer).
2. Pre-flight: run-7 weights archived aali-sft-4k.run7-soupshape;
   re-bootstrapped from phase-d-8k (config verbatim, dropout 0.1, step=0,
   fresh optimizer); brain :20129 killed by exact netstat PID 3552
   (soup.exe serve checkpoint-3873 — cmdline verified first); watchdog
   left running (card_is_safe makes it wait, never fight).
3. Launched detached: AALI_PHASE_C_DATA=D:/hwk-data/soup/sft_scratch.jsonl
   AALI_PHASE_C_STEPS=2500 AALI_PHASE_C_LR=2e-5 launch_detached --log
   phase_c_chain (pid 5632). Brain :20129 is DOWN for the duration —
   restore checkpoint-3873 after the run (no auto-promotion either way).
4. Owner decision after smoke: replace model/scratch/final.pt only on a
   good smoke, same gate as every previous run.

## RUN 8 OUTCOME (2026-09-26 10:32) — shape fixed, capacity still the wall

Run 8 completed clean (exit 0, 2500 steps, 81.9M tokens, ~32 min,
~42.5k tok/s): loss ~2.0-2.5, eval 2.40 -> 2.355 (NOT comparable to run 7's
2.049 — different data shape, different loss surface). Serve-shape smoke:
- EN answer: a CODE attractor fragment `if("%s", "%s"))` — new failure
  mode (code-heavy rows in the mix), not the soup JSON of run 7.
- AR answer: pseudo-Quranic salad, same as run 7 — the phase-d-8k base's
  pretraining attractor survives 2500 SFT steps.
- The FORMAT is now right (rows teach the serving shape; zero soup JSON
  anywhere) — what remains is FLUENCY: the 110M base at ~14.6B total
  tokens still cannot compose natural prose, and SFT cannot add that.
Verdict: DO NOT PROMOTE. model/scratch/final.pt untouched. Run-8 weights
kept at D:/hwk-models/aali-sft-4k (run 7 at aali-sft-4k.run7-soupshape).
NEXT LEVERS (owner decision, honest): (a) keep checkpoint-3873 as the
served brain and treat the scratch brain as research (current state);
(b) distill the promoted teacher into scratch-shaped rows at larger scale
with quality gates; (c) a much longer Phase C budget on sft_scratch
(epochs help fluency less than pretraining tokens do). No auto-promotion.

DECISION (2026-09-26, owner): lever (a) — checkpoint-3873 stays the served
brain, model/scratch/final.pt stays untouched, and lever (b) distillation
is GO as the research path (Phase C task closed; distillation work tracked
in tasks/distill-teacher-scratch.md).

## Run-8 day incident trail (same morning)
- Watchdog fight resolved: the brain watchdog revived :20129 every 120s
  while the chain waited for the GPU (I killed the server twice; it came
  back). Order learned: STOP the watchdog first, then the server pair
  (shim python.exe -> soup.exe child holds the CUDA context — both PIDs).
- :5055 revival race: two app.py processes raced the port after the
  aali_server_watchdog revived one in LOCAL mode while the tunnel was up
  (the 09-25 hazard pattern: an unauthenticated ask served through
  aali.dpdns.org). Fixed per convention: both server watchdogs stopped,
  one app.py relaunched via launch_detached with AALI_API_KEY=<master
  key>; verified honest 404 on /api/ask without a key, locally AND via
  the edge. NOTE: /api/health's mode field reflects the BRAIN config, not
  the auth mode — key gating is proven by the 404, not by mode:"multi-user".
- Brain watchdog relaunched THROUGH the key-mode env (AALI_API_KEY +
  AALI_OWN_MODEL=0 AALI_OLLAMA=0) so its spawn_serve child inherits key
  mode; aali_server_watchdog intentionally left stopped (its local-mode
  revival is what raced the port; revival duty stays with the brain
  watchdog).

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

## Run 6 OUTCOME (19:33-19:49) — loops broken, salad remains: CAPACITY ceiling
Setup: sft_small.jsonl (3,353 rows: 1,448 shortest-JSON tool + 1,841 AR
natural + 28 EN natural/seeds x4), 2500 steps ~ 6 epochs, eval 2.50 -> 1.11.
The deep probe (phase_c_probe.log) also proved the UNTRAINED Phase B base
loops tool-name JSON (string_files x13, make_directory x14) - loop-proneness
is the base model, and run-5's emojis loop is the same JSON attractor.
Smoke verdict: the no-repeat-ngram ban WORKED mechanically (no x14 loops,
no unk glyphs) but both answers remain word salad with language mixing
("pip is able to s...", "أو أو أو.png").
CONCLUSION: trainer (shift, masking, row shape), decoder (penalty, ngram
ban, unk ban) and data (mix audit + rebalance) are all FIXED and test-pinned
- what remains is the BASE MODEL: 110M params + 3.3B pretraining tokens is
below the fluency floor, and no SFT recipe fixes that. Run-6 model archived
as aali-sft-4k.run6-salad-noban-fixes... (see AGENTS.md). Next is a
PHASE-D-SCALE owner decision: (a) keep checkpoint-3873 as the live brain and
treat the own-brain as research; (b) Phase D pretraining continuation
(10-20B tokens, GPU-weeks on the 8GB card) before any SFT retry;
(c) distill from the promoted teacher into the small model with a
serving-shaped, quality-gated corpus. model/scratch/final.pt stays untouched.

## Remaining plan (rest of the window)
1. When training lands: re-probe with _probe_phase_c_brain.py (serve shape).
2. Human decision: replace file-agent/model/scratch/final.pt (copy final.pt
   + config.json) — Aali then codes and chats from his OWN brain.
3. Commit the fixes; keep the API on AALI_OWN_MODEL=0 until scratch lands.