# Phase D stage 4 + Pi-CI revival + night automation (2026-09-23)

- owner: buffy (this PC)
- status: in-progress
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
- [ ] Launch the watchdog detached 12h -> it bootstraps + launches stage 4
- [ ] Pi-CI: pre-seed deps over ssh (torch from PyPI), re-enable HWK PiCI,
      trigger one run, verify result.json
- [ ] Regenerate STATUS.md (clear the false alarm)
- [ ] AGENTS.md SS5 + close this claim + commit (no push)

## Rules honored
- 20% disk rule: D: ~95G free (PASS), X: 321G free; mirrors to X: unchanged.
- Detached-launch convention for the long GPU job (launch_detached.py).
- No kills by image name; GPU checked before any launch (watchdog gate).
- Claim file created before work; no pushes.

## Logs
- phase_d_watchdog.log       - watchdog actions (tonight's card owner)
- phase_d_train_stage4.log   - stage 4 trainer
- D:/hwk-data/pi_ci/*        - Pi-CI ship + result
