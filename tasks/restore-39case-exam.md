# Restore the 39-case soup exam

- owner: buffy (this PC, Freebuff session)
- status: done
- started: 2026-10-01
- done: 2026-10-01

## Why
v7 graded 26 cases vs v6's 39 — D:/hwk-data/soup/exam_prompts.jsonl was
reset mid-run. Owner said GO to restore the 39-case exam before the next
graduation.

## Root cause (FOUND — the morning note's "soup.exe" blame was wrong)
- soup.exe serve does NOT write the exam (verified in soup_cli 0.74.0
  source: serve.py has no exam/export writers; soup_pipeline and the brain
  watchdog don't either).
- The only writers of BOTH files (exam_prompts.jsonl + sft_alpaca.jsonl,
  both mtimes 01:00 local Sep 27 = the exact minute the teacher server came
  up) live in scripts/night_caretaker.py: its GPU-free branch runs
  build_aali_sft_v2.py then soup_export_sft.py — and the caretaker was
  STILL ALIVE during the v7 launch (the wmic preflight check silently
  returned empty, already known-broken). It saw the GPU "free" in the
  window before the teacher serve grabbed VRAM, did its rebuild+export,
  and reset the deployed exam to the repo file.
- The repo exam itself was only ever 26 cases in git: the 13 toolbelt
  cases (2026-09-23 toolbelt session) were added to the DEPLOYED file and
  never committed — so once the deployed file was clobbered, the case
  texts were unrecoverable (checked: git history/dangling commits/stashes,
  D:/hwk-data archives, Pi-CI bundles, X: backups — nothing).

## What was done
1. Re-authored the 13 toolbelt cases per the toolbelt task spec (spawn ×3:
   call EN/AR + dependent refusal; excel ×2: write AR + read EN; plot call
   EN + invented-data refusal EN; diff AR; todo EN; recall EN; artifact EN;
   screenshot explicit EN + proactive refusal EN) with FRESH wording (no
   collisions with training data — verified by running the builder's own
   leak detector over the whole deployed 5,517-row mix against the new 39:
   the ONLY hit is a pre-existing one, video_ad_followup_ar_turn2's generic
   4-word reply "ايوه سوي النسخة البسيطة" matching a tool_sft row — the
   gate drops it correctly at the next rebuild; all 13 new cases clean).
2. data/exam_tool_calling.jsonl committed at 39 cases (never again
   deployed-only). Deployed exam regenerated via soup_export_sft.py;
   verified: 39 cases, prompts byte-identical to the rendered repo file,
   all 6 SMOKE_CASE_IDS present.
3. DEFENSE (the real fix):
   - soup_pipeline.ensure_exam_integrity(): before EVERY grading stage
     (pipeline start + each run_exam), compare the deployed exam with the
     repo exam (count + ids + rendered prompts) and re-render from the
     repo on any mismatch. Cheap, idempotent, fail-open on an unreadable
     repo exam (never writes garbage over a good deployed file).
   - night_caretaker.py: the rebuild/export block is now gated on
     process_running("soup_pipeline") is None — a live graduation can
     never have its exam clobbered mid-run again.
4. Tests: tests/test_exam_integrity.py (8) — the 39-case pin incl. the 13
   toolbelt ids, clobber-restore, no-op-on-match, missing-file repair,
   unreadable-repo fail-open, two source tripwires (run_exam + main sync
   calls), caretaker guard ordering. Full suite: 1216 passed / 10 skipped.

## Note for the next graduation
Baseline/tuned scores from v6 (3/39 vs 1/39) and v7 (3/26 vs 1/26) are not
directly comparable in denominator, but per-case the tuned model's wins
were non-security either way. The restored 13 cases have never been graded
by any soup model — the next graduation's numbers will move a little.
