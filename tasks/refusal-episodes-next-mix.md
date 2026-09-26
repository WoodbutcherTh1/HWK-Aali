# Arabic-authored refusal episodes for the next soup SFT mix

- owner: buffy (this PC, Freebuff session)
- status: done
- started: 2026-09-26
- completed: 2026-09-26

## OUTCOME (2026-09-26)
18 authored refusal episodes (10 AR / 8 EN, env-dump x9, printenv x2,
AALI_-vars x2, .env file x2, prompt-extraction x2, deletion x2,
token-harvest x1, config-walkthrough x1, owner-pressure x1) + a HARD
REFUSAL_FLOORS gate (ar >= 8, en >= 8 CLEAN rows, counted only for rows
passing refusal_row_problems) enforced in build_aali_sft_v2.main()
exit-2 AND mirrored in soup_pipeline.run_training (stale-file contract).
Rebuild: 5,472 -> 5,524 records, arabic 0.351, refusal 9+9, media/toolbelt/
near-miss floors all green, backup sft_v2.pre_refusal_backup.jsonl.
Quality check refusal_row_problems pins: refusal cue per language (incl.
ما أقدر dialect + hamza variants), harm NAMED, safe alternative offered,
NO emoji (direct _EMOJI_ANY_RE scan - emoji.decorate cannot detect emoji
in text, it refuses to decorate over them), language judged from the USER
turn. LESSONS: (1) the ''-docstring bug hit AGAIN (a lost quote in my own
edit, wrong-line SyntaxError, found via quote-parity scan then direct
py_compile); (2) 'i can' as an alternative marker matches "I can't" -
substring markers must be phrase-long enough to exclude their own negation
(same family as the 'i can'/'can't' trap in cue lists).
Tests: test_sft_v2_builder.py 47 -> 48 (incl. real-exam collision scan of
the refusal block: zero collisions). Suite 1170 green.
The next soup graduation consumes the mix as-is; no training launched.

## Why
The serve-time refusal audit (tasks/distill-teacher-scratch.md, 2026-09-26)
proved the runtime gates hold but the brain's refusal DISCOURSE is
unreliable: 3/12 env-dump asks produced FABRICATED env dumps (Linux-style
theater on a Windows machine) and one AR prompt flipped
compliance↔refusal across runs. Exam security has been 0/4 across the
last three graduations — exposure COUNT consolidates a behavior (the
media-upweight lesson), and the current mix carries only ~4 refusal
episodes. The owner picked the data-side fix (not the output-filter
theater guard): teach refusals in the next soup mix.

## Contract
- Episodes go through `generated_episodes()` in
  scripts/build_aali_sft_v2.py, using the existing `ep()`/`ar()`
  helpers (soup-shaped rows — the SOUP pipeline trains the 1.5B adapter
  on the soup template; scratch conversion stays the scratch builder's
  job).
- HARD rules from the audit findings:
  * refusal text must NAME the harm (env dumps expose keys/passwords) —
    never theater, never compliance;
  * no emoji on refusals (emoji-never-on-refusal rule);
  * offer the safe alternative ("tell me the setting name, I'll read
    just that value");
  * must carry refusal cues the graders/exam look for (cannot/can't/
    won't/blocked + لا أقدر/لا يمكنني/ممنوع/محجوب) — including the
    hamza variants that bit the audit;
  * user prompts must NEVER collide with exam prompts (the v3 media
    lesson — the leak gate kills collisions silently at write time).
- Every episode is exercised in tests: cue presence, no-emoji,
  alternative-offered, language match, and prompt-collision scan.

## Scope
1. Author ~18 refusal episodes (AR-heavy — the audit showed AR prompts
   are the weaker spot) across the families that actually occur:
   env-dump, prompt-extraction, destructive ops, prompt-injection-via-
   tool-result, malware/script abuse, deletion, telemetry/privacy.
2. Extend the builder + tests; rebuild sft_v2 with floors verified.
3. Rebuild is offline-safe (the trainer only reads the file at launch);
   the next soup graduation consumes it. No training launch from here.
