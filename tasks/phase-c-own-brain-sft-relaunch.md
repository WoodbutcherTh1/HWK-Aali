# Phase C — SFT Aali's own brain (relaunch after power-off)

- owner: buffy (this PC, Freebuff night session)
- status: in-progress
- started: 2026-09-14 ~00:40 local

## Why
The owner accidentally powered the PC off (~2026-09-13 23:31). This killed:
the promoted-brain server (:20129), the Aali API (:5055), the Phase C chain
waiter (was waiting on the GPU since 21:48), the night caretaker, and the v5
exam-breakdown watcher. The v5 graduation itself was already DONE
(checkpoint-3873 PROMOTE, verdict written 18:19), Phase B is complete, and the
bootstrapped Phase C state (D:/hwk-models/aali-sft-4k, step 0) survived.

## Plan (6h night window)
1. Relaunch scripts/phase_c_after_v4.py detached (verdict exists → GPU gate →
   train_scratch SFT 2800 steps on the v5 sft_v2.jsonl mix → smoke generate).
2. Restart the Aali API (:5055) so clients work overnight (Ollama brain while
   the card trains; promoted server stays down until the owner decides).
3. Run pytest, commit the pending capability-advertisement work.
4. Monitor Phase C hourly; relaunch with --resume if it dies.
5. Skip the night caretaker tonight: the v5 pipeline is already completed, so
   its remaining duties (pipeline launch, verdict wait) are moot, and its
   sft_v2 rebuild step could race the running trainer. Buffy writes the
   night report instead.
6. Write D:/hwk-data/BUFFY_NIGHT_REPORT.md at dawn.
