# Task: image-tool-heavy SFT mix for the next graduation run (sft_v3)

- owner: Buffy (implemented 2026-09-12, ~19:50 local)
- status: steps 1–3 DONE and verified offline (builder episodes + floors gate
  + rebuild). Step 4 (launch) needs the owner's explicit go — the
  checkpoint-2900 adapter is promoted and live-serving on :20129.

## Implementation results (2026-09-12, verified)

- Builder media block replaced: **67 authored episodes** (image ×19, video
  ×12 incl. 4 two-turn consent pairs, read ×8, edit ×8, emoji ×6, error
  recovery ×8 — plus 2 ask-when-vague kept from v2). All schemas match
  file_tools.py (`generate_emoji` takes `prompt`, NOT the v2 bug
  `description`).
- **Floors gate shipped in TWO places**: the builder fails with exit 2
  below per-tool floors (image 12 / video 8 / edit 6 / read 6 / emoji 4,
  `MEDIA_FLOORS`), measured on rows actually WRITTEN; `soup_pipeline.run_training`
  re-checks the file before training so a stale/hand-built dataset fails in
  one second instead of burning a ~4.4h attempt.
- Rebuild verified: 5,391 records (was 5,336), **media rows 7 → 67**
  (generate_image 22 / video 16 / edit 10 / read 11 / emoji 8), arabic
  share 0.345, 0 token-overflow rows, 0 exam leaks in the written file,
  soup.yaml replica check passes.
- Diff vs backup (`D:/hwk-data/soup/sft_v2.pre_sft_v3_backup.jsonl`):
  +60 v3 episodes, +1 conversation-log row (live log grew), −4 OLD media
  episodes intentionally superseded (image-gen-ar, video-plain-rough-en/ar,
  emoji-gen-en — leak-verified clean, replaced by v3 versions). No other
  source changed.
- Tests: 7 new gate tests; full suite **331 passed**.
- Build report kept at `D:/hwk-data/soup/sft_v3_build_report.json`.

## Diagnosis (evidence, not vibes)

The 09-12 graduation adapter scored 4/26; every media case failed
(img_basic_en/ar, img_missing_path_en, video_ad_claim en/ar + followups),
answering "I can't draw pictures" instead of emitting tool JSON.
Measured cause, from the actual artifacts:

1. `D:/hwk-data/soup/sft_v2.jsonl` (the file attempt 7 trained on) has
   **7 media-touching rows in 5,336 (0.13%)**; `generate_image` appears in
   just 3, `generate_video` in 4. The exam grades 5 media cases — 0.13%
   exposure cannot teach a behavior.
2. The builder (`scripts/build_aali_sft_v2.py`) authors 13 media episodes,
   but the build report's source census shows only 7 survived:
   **6 died as "exam leaks"** because they were written using the exam's
   exact user prompts ("Draw me a picture of a mountain lake at dawn." is
   literally `img_basic_en`). The leak gate worked as designed — the
   episodes were authored wrong.
3. Secondary: the surviving media rows are single-turn; the exam's
   follow-up cases (`video_ad_followup_*`) need consent-then-call two-turn
   behavior.

So the fix is builder-side (author media episodes with NEW wording, many
of them, incl. multi-turn), not a config change and not a trainer change.

## Plan

### 1. Builder: replace the 13-episode media block with a 60-episode media block

Target ≈60 media rows (1.1% of ~5,400) — ~8x today, without drowning the
general mix. Structure (all with real file_tools.py schemas; `prompt` +
`path` required args for generate_image/generate_video):

- **generate_image ×18** (EN 9 / AR 9): 3 paraphrase families × 6
  ("draw me X" / "I want a picture of X" / "make an image showing X") with
  DIFFERENT subjects (city, cat, desert, coffee, football, sunset…) and
  different target paths (`images/*.png`). Zero overlap with exam wording.
- **generate_video ×12** (EN 6 / AR 6): plain clips + the honest-refusal →
  consent → call two-turn pairs (mirrors video_ad_claim/followup but with
  new scenarios: birthday reel, product b-roll, rain window).
- **read_image ×8** (EN 4 / AR 4): OCR on screenshots/signs/documents.
- **edit_image ×8** (EN 4 / AR 4): resize/crop/rotate/grayscale ops with
  `path` + `output` + `op` (never overwrite).
- **generate_emoji ×6** (EN 3 / AR 3): sticker requests with prompt+path
  (`prompt` is the real file_tools.py arg — the v2 draft said `description`;
  implemented with the correct key).
- **error-recovery pairs ×8**: tool result `{"ok": false, ...}` AFTER a
  media call → assistant admits + offers fix (no invented success) — the
  hallucination family that already works, extended to media.

Every episode id suffixed `-v3`; wording checked against
`load_exam_prompts()` hashes. Multi-turn episodes use the existing
messages format (user → assistant tool JSON → user → assistant), which the
trainer already consumes (conversation-log rows are multi-turn).

### 2. Builder: hard gate so this regression can never recur

After assembly, count rows containing each media tool name and fail the
build (non-zero exit) below floors: generate_image ≥ 12, generate_video ≥ 8,
edit_image ≥ 6, read_image ≥ 6, generate_emoji ≥ 4. Print the census.
(2026-09-10's lesson was silent write-drops; 09-12's was silent exam-leak
drops. Floors make BOTH loud.)

### 3. Offline verification (no GPU needed — the part that failed last time)

- Rebuild sft_v3; assert: 0 exam leaks, media census ≥ floors, Arabic share
  stays ≥ 30%, total ~5.3-5.5k rows, token-overflow rows = 0
  (`token_overflow_rows` gate, TRAIN_MAX_LENGTH=768).
- Replica check: run the soup data loader on the file (the 09-12-night
  lesson: replicate soup's own path before believing anything).
- Smoke probe the CURRENT checkpoint-2900 on CPU before training to have a
  before-number (expect the 4/26-ish behavior set).

### 4. When the owner says go (GPU arc, ~5-5.5h)

The pipeline now handles everything itself; relaunch is one command:

```
scripts\launch_detached.py --log soup_v3 -- .venv/Scripts/python.exe scripts/soup_pipeline.py
```

Order of operations: stop the :20129 promoted server (it would fight for
VRAM) → pipeline serves teacher → baseline exam → 3,804-step train
(~4.4h) → tuned exam → verdict. Promoted checkpoint-2900 is archived by
soup's own flow before the new run overwrites `tuned/`.

### 5. Success bar

Verdict PROMOTE with the 5 media cases ≥ 3/5 (today: 0/5) and total ≥ 4/26
(today: 4/26). If media take but total drops, the mix overfit — rebalance,
don't promote. Decision rule stays the pipeline's: strictly beat baseline.

## Non-goals

- No trainer/hyperparam changes (768 ctx, 3 epochs, QLoRA r32 are proven
  on this card; the 09-12 run trained cleanly to 77%).
- No new exam cases (grading must stay comparable to baseline 1/26).
- No launch until the owner approves — the current adapter is LIVE.
