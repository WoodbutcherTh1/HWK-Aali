# Phase D stage 4 + Pi-CI revival + night automation (2026-09-23)

- owner: buffy (this PC)
- status: done
- started: 2026-09-23

## Morning findings (09:03-11:40)
1. Phase D stage 3 FINISHED 2026-09-23 early morning: `done: ... step=4000
   tokens=131,072,000`, eval 1.91 -> 1.866. All 11 corpora have manifests.
2. STATUS.md false alarm: stage 3 screamed STALLED for hours because
   status_digest only recognized the watchdog's COMPLETE wording, not the
   trainer's `done:` exit line, and only scanned stage logs 1-3.
3. HWK PiCI scheduled task DISABLED -> 233.9h of "Pi dead". The Pi itself is
   UP (192.168.1.15, SSH ok, PyPI reachable; address .9 is squatted).
   Last remote failure: "deps failed" - pyproject `pip install -e` can never
   work on ARM (bitsandbytes etc.), fallback needs torch (no local aarch64
   wheel; net is OK now).
4. night_caretaker.py duty ended 2026-09-21 20:32 -> MORNING_REPORT.md stale
   two days. night_caretaker is soup-specific; tonight the card is Phase D's.

## Steps
- [x] status_digest.py: scan stage logs 1-5, treat trainer `done:` as
      COMPLETE, weights rows for all three finished dirs (tests added)
- [x] phase_d_watchdog.py: stage 4 (phase-d-wide -> phase-d-all, ALL tokenized
      corpora, 4000 steps) + MORNING_REPORT.md written on window end (tests)
- [x] Launched the watchdog detached 12h -> it bootstrapped phase-d-wide ->
      phase-d-all and launched stage 4 on all 11 corpora at 11:53
- [x] Pi-CI: deps pre-seeded over ssh (torch 2.14.0, numpy, sentencepiece),
      HWK PiCI re-enabled, runs triggered (collection now clean: 780 tests)
- [x] STATUS.md regenerated - stage-4 rows correct, no false STALLED
- [x] AGENTS.md SS5 + commit 76fac03 (amended; stale 09-22 stage-table test
      updated to the 4-stage contract; full suite 785 green). No push.

## Lessons this session
- A watchdog-test fixture MUST fake launch_detached: the deferral test
  spawned a REAL detached train_scratch.py mid-suite (killed by exact PID
  86816 after cmdline verification; junk stage-4 log + err file deleted;
  no mirror dir was created). Fixture now fakes launch_detached globally.
- The stage-3 deferral guard fires when ADVANCING TO stage 3, not past it.
- Pi deps: pyproject editable install never works on ARM (bitsandbytes etc.);
  the fallback set is flask requests pytest torch + numpy + sentencepiece.
- schtasks /Enable breaks under MSYS path mangling - use PowerShell
  Enable-ScheduledTask / Start-ScheduledTask.

## Night ownership (deliberate)
- night_caretaker.py is NOT relaunched: it is soup-specific and the v5
  graduation is done. phase_d_watchdog.py owns the card tonight (12h window,
  relaunches stage 4 on TDR/OOM death, writes MORNING_REPORT.md at window
  end). When stage 4 completes it reports 'all stages complete' and leaves
  the card free for the next human decision (Phase C SFT retry on the wider
  base).

## Rules honored
- 20% disk rule: D: ~95G free (PASS), X: 321G free; mirrors to X: unchanged.
- Detached-launch convention for the long GPU job (launch_detached.py).
- No kills by image name; GPU checked before any launch (watchdog gate).
- Claim file created before work; no pushes.

## Logs
- phase_d_watchdog.log       - watchdog actions (tonight's card owner)
- phase_d_train_stage4.log   - stage 4 trainer
- D:/hwk-data/pi_ci/*        - Pi-CI ship + result
