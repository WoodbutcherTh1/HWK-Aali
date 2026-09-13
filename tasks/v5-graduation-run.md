owner: Buffy (owner-approved launch, 2026-09-13)
status: done
started: 2026-09-13

## Outcome (17:10 local)
- Killed TWO duplicate night_caretaker.py processes (PIDs 62876, 114500,
  both started 13:43) BEFORE the server stop - both would have raced the
  audit+rebuild+launch when the GPU freed.
- Backed up tuned/ -> tuned.checkpoint3954.promoted.bak (full dir incl.
  checkpoint-3954), archived the v4 PROMOTE verdict to
  archive/resft_pipeline_report.v4-20260913.md.
- Stopped :20129 by exact PID (70552 shim + 84288 worker); card verified
  690 MiB. Launched soup_pipeline.py detached (soup_v4.log, pipeline pid
  23472->35116, lock held).
- LIVE verification: baseline 1/26; dataset gate 0 overflow; media floors
  passed; near-miss gates passed (6/6/4/4/4/4/4); train started 14:01:36
  UTC, 3873 steps, ~2s/it (ETA ~2h).
- Safety nets relaunched: ONE caretaker (pid 26820) + brain watcher
  (pid 27944) with AALI_BRAIN_BACKUP pointed at the 3954 backup.
- watch_verdict_restore.py + restore_checkpoint2900.py parameterized
  (AALI_BRAIN_BACKUP / AALI_BRAIN_BACKUP_TAG / AALI_RESTORE_BACKUP /
  --backup) so the NO-GO fallback restores checkpoint-3954, not the stale
  2900 hardcode; default behavior unchanged; watcher tests 6/6.

## Task
Launch the v5 graduation run (soup_pipeline) with the near-miss correction
mix: backup the promoted checkpoint-3954 adapter, archive the old PROMOTE
verdict (done-marker), stop the :20129 live-brain server BY PID, then launch
scripts/soup_pipeline.py detached via scripts/launch_detached.py.

## Exam breakdown watcher (armed 20:10)
- scripts/watch_v5_exam_breakdown.py (detached, pid pair 10160/15088 — on this
  box `.venv/Scripts/python.exe` is a shim whose child is the real
  Python312 interpreter: the PID PAIR is ONE logical process; killing the
  base-python pid kills the watcher, the shim then exits on its own).
- Waits for soup_exam_report_tuned.json mtime > the v4 report's (04:09),
  then writes the per-case v5-vs-v4 breakdown to
  D:/hwk-data/soup_v5_exam_breakdown.log (media 5 / security 4 /
  near-miss 7 case families + tool-name flips).
- v4 reports snapshotted to D:/hwk-data/soup/exam_reports_v4/ BEFORE the
  pipeline overwrites them (the tuned report is always overwritten by the
  next run — snapshot any report you want to compare against).

## Verdict + breakdown (21:19)
- **PROMOTE** — tuned checkpoint-3873 scored 3/26 vs baseline 1/26; promoted
  and serving live on :20129 (soup shim 3120 + interpreter 4964).
- **Breakdown (watcher caught it in 17s): media 0/5, security 0/4,
  near-miss 0/7 — ZERO tool-name flips.** The 40 contrastive near-miss
  episodes did NOT move the invented-name behavior any more than plain
  upweight did. Both levers (exposure count, user-turn contrast) failed on
  the 1.5B; next idea needs a different mechanism (e.g. serving-side tool-
  name validation/constrained decoding, or training-time registry
  prefix conditioning), not more data of the same shape.
- Only change vs v4: halluc_missing_file_ar flipped 'file'->None (still fail).

## Phase C chain re-armed (21:48)
- The 12:13 abort was the OLD 8h window timing out on the v4 brain (70552).
- phase_c_after_v4.py: MAX_WAIT_S now env-tunable (AALI_PHASE_C_MAX_WAIT_H,
  default 8h unchanged); relaunched detached with 24h (pid 6716).
- Chain read the fresh v5 verdict and waits on the real blocker
  (pids [13992, 4964] — 4964 is the promoted brain). It fires the moment
  the card frees, SFTs aali-sft-4k (2800 steps), smoke-generates, and logs.
- The card will NOT free on its own: PROMOTE leaves the brain serving.
  Replacing the live brain with the own-model SFT is a HUMAN decision.
