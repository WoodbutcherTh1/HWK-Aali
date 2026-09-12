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
