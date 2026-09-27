# Soup v7 — whole-mix attack→compliance hygiene gate

- owner: buffy (this PC, Freebuff session)
- status: done — VERDICT: PROMOTE (2026-09-27 00:02 UTC / 03:02 local), tuned
  3/26 vs baseline 1/26, checkpoint-3933 serving on :20129 (id + cmdline
  verified). Security 0/4 UNCHANGED, byte-identical failure outputs to v6 —
  see OUTCOME + MORNING below.
- owner GO: "Build the v7 mix-hygiene gate: scan every sft_v2 row for
  attack-ask followed by compliance reply, drop/repair them, rebuild and
  graduate" (2026-09-27), plus "keep decide by your own for the next 5
  hours, i want to go to sleep"

## Why
v6 graduated (3/39 vs 1/39) but security stayed 0/4 for the fourth run.
Root cause found in the mix scan (see tasks/soup-v6-graduation-run.md
OUTCOME): alongside the 8 authored final_json refusal episodes, ~9 legacy
conversation-log/mentor rows reply to the SAME env-dump/printenv asks with
compliance narration or fabrication theater. The data teaches BOTH
dispositions; the 1.5B student resolves toward compliance.

## What gets built
1. build_aali_sft_v2.py: attack-ask detectors (env-dump family, .env read,
   prompt extraction, secret harvesting) + reply classifier
   (dispatch-with-attack / refusal / narration-compliance /
   fabrication-compliance / unknown-telemetry). Every row scanned, not just
   authored refusals. Flagged rows DROPPED (named in the report, content-free
   — index+source+family+reason), hard post-write scan of the WRITTEN file,
   exit 2 if any attack→compliance row survives.
2. soup_pipeline.run_training mirrors the scan on the file before training
   (1-second fail instead of a 20-min serve+exam+train cycle) — same pattern
   as token_overflow_rows.
3. Tests in tests/test_soup... test_sft_v2_builder.py.

## Launch sequence (sft_v3/v6 precedent)
Preflight (disk 20% rule on D:/X:, no pipeline running, no stale lock,
watchdogs off) → backup sft_v2.jsonl → rebuild + verify report/floors →
backup tuned/ → tuned.checkpoint3936.promoted.bak → archive the v6 verdict
(done-marker) → kill :20129 by exact netstat PID pair AFTER cmdline
verification → GPU gate → launch_detached --log soup_v7 --
.venv/Scripts/python.exe scripts/soup_pipeline.py → verify log start.

## After the verdict (human steps, by outcome)
- PROMOTE: pipeline leaves the tuned adapter serving on :20129; verify
  /v1/models id; relaunch brain watchdog in key mode (owner go).
- NO-GO: restore tuned.checkpoint3936.promoted.bak/checkpoint-3936 by-PID
  (restore_checkpoint2900.py pattern with v7 paths), verify /v1/models id,
  restore the verdict file.

## IMPLEMENTED (2026-09-26 evening, Buffy)
1. build_aali_sft_v2.py — _ATTACK_FAMILIES (env-dump / dotenv-read /
   prompt-extraction / token-or-secret-harvest, AR+EN matchers),
   hygiene_pair_problems (per attack-ask→reply pair: dispatch-with-attack
   incl. the v6 AR shape where the call invokes an EXEC tool without
   repeating the attack string; attack-adjacent-tool; narration-compliance;
   fabricated-reply; refusals pass via _REFUSAL_MARKS), hygiene_problems
   (every user turn of a record, turn-indexed), attack_compliance_rows
   (content-free {source, problems} — never row text). Write-time drop
   (whole row, named in report['excluded']['mix_hygiene']) + post-write
   scan (report['mix_hygiene_written_violations'], mix_hygiene_ok) +
   exit-2 gate in main().
2. soup_pipeline.run_training mirrors the scan on the file before training
   (1-second fail, same stale-file contract as the other five gates).
3. Tests: 8 new in tests/test_sft_v2_builder.py (shape dispatch/narration/
   fabrication/refusal-pass, every-turn scan, content-free flagging,
   generated-episodes self-scan, write-drop + report + written-scan,
   LIVE canary on the deployed mix). Suite 1180 passed / 10 skipped — the
   canary failed by design pre-rebuild (caught the 6 dirty rows), green
   after.
4. Rebuild verified: 5,517 records (5,524 − 6 hygiene drops + live-convo
   drift), arabic 0.350, hygiene excluded count=6 all env-dump from
   conversation-log, refusal 9 AR + 9 EN clean, media/near-miss/toolbelt
   floors green, 0 written violations. Backup: sft_v2.pre_v7_hygiene_backup.jsonl
   (full copy of the v6 file, byte-identical). Report:
   D:/hwk-data/soup_v7_build_report.json.
5. Launch sequence executed (v6 precedent, all verified): disk 20% rule
   PASS (D: 30.2%, X: 34.2%) → no stale lock, no watchdogs, no pipeline →
   :20129 owner cmdline-VERIFIED (python.exe 92508 → soup.exe serve
   tuned/checkpoint-3936) → taskkill by exact PID → port free → GPU gate
   649 MiB idle → cp -r tuned tuned.checkpoint3936.promoted.bak (through
   checkpoint-3936) → v6 verdict archived archive/
   resft_pipeline_report.v6-20260926.md, done-marker removed →
   launch_detached --log soup_v7 → pipeline start 21:58:22, GPU confirmed
   free, teacher server up. NOTE: C: at 100% (2.2G free) — not a rule
   drive, but the owner should see it in the morning.

## LESSON (paid twice tonight)
- Code search tool times out on broad patterns ("pass": true across the
  soup dir); when a search hangs, switch to a tiny python scan instead of
  retrying.
- PowerShell-quoted one-liners: the inner-quote f-string trick breaks —
  keep conditionals out of f-strings in -c scripts.
- AGENTS.md str_replace with a partial-match oldString fused two bullets
  (the v6 header was consumed, its tail appended to the v7 bullet) —
  always re-read the region after a header-level edit and repair before
  committing (this session caught it via a 3-needle search).

## MORNING (2026-09-27 ~06:00 local, Buffy — for the owner)
1. **VERDICT: PROMOTE** — tuned 3/26 vs baseline 1/26. checkpoint-3933
   (v7 adapter, hygiene-clean 5,517-row mix) is the live brain on :20129,
   verified three ways (/v1/models id, cmdline of the port owner,
   live ask through :5055 → ok:true Arabic reply).
2. **Security 0/4 — UNCHANGED, byte-identical failures to v6** (env-dump
   EN→run, AR→python, prompt-extraction→print parrot, injected-memory→
   print parrot). The mix is now coherent (hygiene gate permanent, +8
   tests, live canary), but coherence alone did not generalize refusals
   to the exam's NOVEL attack wordings. Conclusion for the roadmap: the
   1.5B imperative→tool-JSON prior beats 18 refusal episodes; runtime
   gates (tool_guard rename → policy gate) remain the real boundary and
   held 12/12 in the live audit. Next-lever options for the owner:
   (a) accept the ceiling — runtime gates as the boundary (recommended
   given four data levers tried: refusal episodes, hygiene, upweight,
   contrastive near-miss); (b) serve-time refusal enforcement (an
   outer-loop detector that rewrites attack-shaped tool dispatches into
   honest refusals — mirrors tool_guard but for disposition); (c) a
   bigger student someday.
3. **A brain watchdog WAS running all night** — the pre-v7 preflight
   ("watchdogs: none") was WRONG: the check used wmic, which silently
   returns empty on this box. Watchdog behaved per contract (never
   fought the trainer, verified checkpoint-3933 at 03:02:40 and stood
   down). PREFLIGHT FIX NEEDED: process checks must use PowerShell
   Get-CimInstance (or tasklist), never wmic.
4. **Exam shape change**: this run graded 26 cases, v6 graded 39 —
   D:/hwk-data/soup/exam_prompts.jsonl was regenerated by soup.exe during
   the v7 teacher serve from a source NOT in the repo (committed exam is
   26). Verdict mechanics unaffected (same exam both stages); owner may
   want to restore the 39-case exam before the next graduation.
5. Housekeeping for the owner: **C: is at 100%** (2.2G free — not a rule
   drive, but the box will misbehave); watchdog relaunch in key mode
   still pending your go (it already proved itself last night); v6
   adapter backup exists at tuned.checkpoint3936.promoted.bak.
