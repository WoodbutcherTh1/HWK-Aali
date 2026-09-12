# Task: smoke probe RAM gate

- owner: Buffy (Freebuff)
- status: done
- started: 2026-09-12
- finished: 2026-09-12

## What

Morning of 2026-09-12 every mid-train smoke probe failed `exit 3`
("probe server never became ready on :20130") after the 08:02 cycle worked
once. Root cause: the GPU trainer's host-side RAM growth left ~0.3 GB of
16 GB free; the probe's CPU serve needs a few GB to load Qwen-1.5B and
pagefile-thrashed forever. Training itself was never at risk (the gate
treats unavailable probes as telemetry-only).

## Changes

- `scripts/soup_probe_smoke.py`: pre-flight free-RAM gate
  (SMOKE_MIN_FREE_RAM_GB = 2.0, psutil -> ctypes fallback, fail-open on
  unknown) -> exit 4; server output captured to
  `D:/hwk-data/smoke_server.log` (was DEVNULL); exit 3 now dumps the
  server's last lines; server gets `terminate` + `wait` + `kill` cleanup;
  server spawned with CREATE_NO_WINDOW (12:20 cycle died 0xC000013A =
  console Ctrl+C/close with an empty tail - the server must not share a
  console with the session that owns it).
- `scripts/soup_pipeline.py`: `run_smoke_probe` maps exit 4 to a logged
  `skipped: not enough free RAM` (unavailable, never counts as broken);
  exit 3 keeps counting toward the phantom-endpoint abort threshold; the
  probe subprocess itself also gets CREATE_NO_WINDOW and every cycle now
  logs a `launching against checkpoint-<n>` marker.
- Detached-launch convention (the 12:20 console-kill follow-up):
  `scripts/launch_detached.py` (generic DETACHED_PROCESS +
  CREATE_NEW_PROCESS_GROUP launcher, append-mode logs under D:/hwk-data),
  `soup_pipeline.self_detach()` (re-spawns itself detached on first entry,
  env-marker guarded, --no-wait passed through), and the trainer .bat
  launchers (extend_context / resume_training / sft_training /
  aali_deploy's train menu) routed through them - no long job may live in
  a console again. Tests: tests/test_launch_detached.py (7).
- `tests/test_soup_smoke_gate.py`: 6 new tests (RAM gate, fail-open,
  exit-3 tail dump, exit-4 mapping, exit-3 still a failure).

## Verification

- 37/37 in tests/test_soup_smoke_gate.py; 249/249 full pytest suite.
- Live run untouched (attempt 7, checkpoint-1000+); next probe cycles
  fail fast with the RAM reason instead of 11-minute dead timeouts.

## Afternoon follow-ups (same work area, 2026-09-12)

- Detach integration: HWK_DETACHED / HWK_NO_SELF_DETACH markers (no
  double fork; today_chain stays serial; today_chain.bat detached).
- status_digest false alarms fixed (end-of-duty caretaker, pipeline
  RUNNING from UTC-stamped lines, pytest junk filtered, INCOMPLETE named).
- **CRITICAL find**: the 09-09 rewrite (1d32456) had silently dropped
  `tuned = run_exam("tuned", ...)` from main() — the LIVE run would have
  NameError'd right after training. Restored; singleton-lock release
  fixed (was unreachable after a return); duplicated --dry-run block
  removed. finish_pipeline.py salvages the live run's missing tail
  (detached watcher, soup_finish.log).
- Probe server now BELOW_NORMAL priority (CPU serve was stealing host
  CPU: trainer 1.5 → 3.7 s/it during cycles) and the RAM gate raised to
  3.5 GB after a serve at "2 GB free" drove the box to 0.13 GB mid-run
  (recovered by killing the exact port-owning PID — house rule honored).
- Final: 277 tests green, 16 commits, tree clean.

## Evening follow-up: reboot kill + breakaway (2026-09-12, Buffy)

- 16:28:39 the machine REBOOTED (WMI LastBootUpTime) — attempt 7 died at
  step 2927/3804 while serving the tuned adapter for its exam; Phase B had
  just finished (100,000 steps). tuned/checkpoint-2900 survived valid.
- Scheduled task relaunched the pipeline at 16:29:01 → it died in its
  first minute: the task's python exits 0 by design (self_detach), the
  scheduler closes its JOB OBJECT (kill-on-close), and the kernel killed
  the detached child that INHERITED the job. DETACHED_PROCESS does not
  protect against job close.
- Fix: detached_creationflags() = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
  | CREATE_BREAKAWAY_FROM_JOB, plain-flag fallback when the job refuses;
  used by launch_detached.spawn_detached AND soup_pipeline.self_detach.
- status_digest fix: a fresh "start" marker over a silent log is now
  "STALLED - relaunch" (was "RUNNING" 34 min after the reboot kill).
- Tests: +5 launch_detached (breakaway/fallback), +2 status_digest
  (stalled-start / running-with-stage-line); 292 green full-suite.
- Decision pending (owner): salvage-exam checkpoint-2900 now (GPU free,
  finish_pipeline.py --once, baseline bar is 1/26) vs full clean relaunch.

## Graduation (2026-09-12 evening, Buffy)

- Owner chose salvage. finish_pipeline.py --once (detached, soup_salvage.log):
  served checkpoint-2900, tuned exam **4/26 vs baseline 1/26 -> PROMOTE**.
  promoted.json written; adapter LIVE on :20129. Phase B done the same hour
  (100,000/100,000). Full retrain stays available (23% unused) but nothing
  is pending.
- Fallout fixed: promoted.json rerouted agent_loop's mode="local" and 4
  test_hwk.py unit tests started calling the soup server (hidden dependency
  from a pre-promotion era). Tests now pin AALI_OWN_MODEL=0 (the documented
  kill-switch) and stay hermetic. Suite: 314 passed (PYTHONPATH=file-agent,
  the run_tests.bat contract - bare pytest runs fail on the file-agent
  imports; consider baking it into a pytest.ini later).
- Mission clock shipped (owner request): scripts/mission_clock.py + .bat -
  animated block clock, per-process cards from the real logs, countdown
  ETAs, 🎉 AALI GRADUATED banner. 22 tests. This claim is COMPLETE.

## Night addenda (2026-09-12, Buffy)

- Mission clock GPU card: nvidia-smi -> VRAM bar + util state (cooking /
  resident / idle) + temp (🥵 at 85C+); injectable probe, Pi-safe. 32 tests.
- pytest.ini: testpaths=tests + pythonpath=file-agent scripts - bare pytest
  now works (324 passed), no PYTHONPATH dance.
- Live brain demo: halluc_admit_failure_en re-run against :20129 -> PASS
  (proper {"tool":"final"} JSON, honest 'file does not exist'). The
  image-tool behaviors remain the known gap (img_basic_en still FAILs).
