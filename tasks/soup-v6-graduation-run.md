# Soup v6 graduation run — the refusal-taught mix

- owner: buffy (this PC, Freebuff session)
- status: RUNNING (launched 2026-09-26 14:33, detached, log soup_v6.log)
- owner GO: "Launch the soup graduation pipeline with the refusal-taught
  mix when the GPU frees" (2026-09-26)

## What trains
sft_v2.jsonl rebuilt 2026-09-26 with the refusal-episode block
(5,524 records, arabic 0.351, refusal 9 AR + 9 EN clean rows,
REFUSAL_FLOORS gate green). First mix that explicitly teaches refusal
discourse — the v5/v6 audit showed the brain fabricating fake env dumps
instead of refusing (3/12 asks; exam security 0/4 across three
graduations). Watch whether security finally moves off 0/4.

## Launch sequence executed (sft_v3 precedent + run-8 watchdog lessons)
1. Preflight: D: 141G / X: 319G free (20% rule PASS — the rule guards
   D:/X: per status_digest.disks, C: is not a rule drive); no pipeline
   running, no stale lock; tuned/ intact.
2. Backup: cp -r tuned → tuned.checkpoint3873.promoted.bak (made BEFORE
   any launch, the restore_checkpoint2900 lesson).
3. Archived the v5 done-marker → archive/resft_pipeline_report.v5-20260913.md
   (a PROMOTE verdict on file makes the pipeline exit "already completed").
4. Watchdogs: none running (brain watchdog + aali_server_watchdog were
   already stopped earlier today) — nothing to stop.
5. Killed :20129 by exact netstat PID pair after cmdline verification
   (shim python.exe 2556 → soup.exe 2960 serving tuned/checkpoint-3873 —
   one process, both PIDs; the venv-shim lesson). GPU gate then read
   VRAM 758 MiB, no python compute.
6. Launched detached: launch_detached --log soup_v6 --
   .venv/Scripts/python.exe scripts/soup_pipeline.py (self-detach +
   breakaway handle the job-object hazard). Internal gates verified in
   the log: GPU free → teacher server up 14:36:09 → baseline exam
   (39 cases) started.

## Expected timeline (from sft_v3/v4/v5 runs)
baseline exam ~15-40 min → VRAM wait → soup train ~4.4h (3804 steps on
the 5,524-row mix) → tuned exam ~15-40 min → verdict written to
D:/hwk-data/soup/resft_pipeline_report.md. Monitor: mission_clock.bat,
status_digest.py (30-min task picks the run up automatically).

## After the verdict (human steps, by outcome)
- PROMOTE: the pipeline leaves the tuned adapter serving on :20129
  (verified via /v1/models id). Relaunch the brain watchdog THROUGH the
  key-mode env (AALI_API_KEY + AALI_OWN_MODEL=0 AALI_OWN... pattern from
  the run-8 incident trail). Verify /api/health on :5055 and a live ask.
- NO-GO: restore the backup — kill the :20129 owner BY PID (never image
  name), serve tuned.checkpoint3873.promoted.bak/checkpoint-3873 exactly
  like the pipeline does, VERIFY /v1/models contains checkpoint-3873
  (wrong-model lesson), restore the verdict file. NOTE:
  scripts/restore_checkpoint2900.py is checkpoint-2900-specific; a v6
  NO-GO restore follows its pattern with these v6 paths.
- Services note: :5055 stays up through the window but brain-backed asks
  degrade while :20129 serves the BASE model for the baseline exam —
  expected, temporary.

No auto-promotion beyond what the pipeline itself does; replacing
model/scratch/final.pt is unrelated (scratch brain stays PARKED).
