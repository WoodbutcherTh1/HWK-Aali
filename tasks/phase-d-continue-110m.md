# Phase D — continue the 110M own-brain (owner decision 2026-09-21)

- owner: buffy (this PC)
- status: in-progress
- started: 2026-09-21

## Owner decisions (asked 2026-09-21)
1. **Path**: CONTINUE the 110M model (not a 300M rebuild, not distill-only).
   +2-4B more pretraining tokens before any SFT retry.
2. **Data**: code (dev) + law + medical corpora (owner: "more Coding and Dev,
   Law, Medical"). Arabic expansion deferred — 3,622 arabic shards exist.
3. **Uptime**: PC stays on 24/7 — full-length runs are planned.
4. Pending Node/SaaS work committed first (a90c46f), suite 706 green.

## Why
Phase C run 6 concluded the trainer/decoder/data are all fixed and
test-pinned; the remaining wall is CAPACITY (110M params, 3.3B pretraining
tokens seen). Continuing pretraining on more + newer data is the owner's
chosen lever before any SFT retry.

## Steps
- [x] Commit pending Node work (a90c46f)
- [x] Revive services: brain :20129 (checkpoint-3873) + API :5055
- [x] hwk_paths: added missing HWK_RAW_DIR override (heavy raw now
      redirectable to X:)
- [ ] Downloads: medical -> law -> code (12GB cap) into X:/hwk-data-raw/raw/
      (detached, corpus_dl.log)
- [ ] Tokenize new corpora into D:/hwk-data/tokens/<name>/ (32k BPE,
      tokenize_corpus.py)
- [ ] Phase D pretrain resume: train_scratch.py --resume from
      D:/hwk-models/context-4k/final.pt, ctx 4096, +2-4B tokens over
      arabic+pile+instruct+orca_math+reasoning+code+law+medical
      (verify bf16 + grad checkpointing fit the 8GB card at ctx 4096)
- [ ] After Phase D: re-run Phase C SFT recipe (all fixes now in) +
      _probe_phase_c_brain.py serve-shape smoke
- [ ] Human decision gate: replace file-agent/model/scratch/final.pt only on
      a clearly better smoke (owner call)

## Rules honored
- 20% disk rule: D: 102G free (22%) PASS — heavy raw redirected to X:
  (336G free) via HWK_RAW_DIR; X: mirror rule for checkpoints unchanged.
- Detached-launch convention (launch_detached.py) for every long job.
- No process kills by image name; no pushes; tasks/ claim before work.

## Logs
- corpus_dl.log        — corpus downloads
- phase_d_train.log    — Phase D pretraining (when launched)
- brain_serve.log      — promoted brain :20129
- api_server.log       — Aali API :5055
