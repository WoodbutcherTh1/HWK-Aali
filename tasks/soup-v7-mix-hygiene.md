# Soup v7 — whole-mix attack→compliance hygiene gate

- owner: buffy (this PC, Freebuff session)
- status: RUNNING (v7 graduation launched 2026-09-26 21:58, detached, log
  soup_v7.log; owner asleep — blanket autonomy granted until ~03:00)
- started: 2026-09-27
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
