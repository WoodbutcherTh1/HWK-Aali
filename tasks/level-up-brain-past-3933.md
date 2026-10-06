# Level the brain up past checkpoint-3933

owner: pc/agent
status: in-progress
started: 2026-10-06

## Owner request (delegated)
> "add more database for aali do train let the brain be more than 3933,
> do level up ... u have all premesion to do all"

## Measured state (2026-10-06)
- CORRECTED by re-measure (same day, disk_probe.py + Get-Volume AGREE):
  C: 53 GB free (11.9%) · D: 77 GB (16.6%) · X: 299 GB (31.6%) →
  **the 20% training rule FAILS on C: and D: — NO training until cleaned.**
  The bullet below had used% labelled as free% (86.4/82.2/67.9 are the USED
  shares); STATUS.md's "D: 18%" was right, not stale.
- ~~Disks: C: 86.4% · D: 82.2% · X: 67.9% free → the 20% training rule PASSES~~
  (inverted reading — see the correction above)
- GPU: RTX 3070 8 GB, only the promoted soup server resident (PID 15136,
  cmdline-verified `soup.exe serve --model .../checkpoint-3933 --port 20129`)
- Phase B: step 100,000/100,000 **COMPLETE**
- Phase D stages 1-5 **COMPLETE** (stage 5 = ctx 8192, 196,608,000 tokens)
- Nothing has trained since 2026-09-23 — the card has been idle ~13 days
- Token shards intact: pile 8005, code 4153, arabic 3623, law 1214, law2 752…

## The decision and WHY
Re-running soup QLoRA on the same 1.5B base is a **measured dead end**:
four separate graduations all landed at 3/26 → 3/39 with security 0/4. A fifth
identical run buys a known-null result. The ceiling is the **base model**, not
the recipe, so "bigger brain" has to mean a bigger base.

Card reality: the served 1.5B holds ~3.9 GB of the 8 GB.
- **Qwen2.5-3B-Instruct** is the honest step up: ~2× the parameters, ~2 GB at
  4-bit, and at inference it costs LESS VRAM than the 1.5B serve it replaces.
- 7B is not viable alongside a served brain on 8 GB (soup.yaml already says so).

## Plan
1. Pull the 3B and **A/B it live against checkpoint-3933** on the exact asks
   that failed today (disk space) — promotion is measured, not asserted.
2. If it wins, run the soup graduation on the 3B base (its own PROMOTE gate).
3. Phase D stage 6 afterwards for the own-brain track (more corpora, more
   context) — one GPU job at a time, the card cannot host two.

## A/B RESULT (2026-10-06 evening — MEASURED, verdict: NO PROMOTE)
Both models warm, same fixed harness, full-reply verdicts:

| check | 3933 (1.5B tuned) | 3B (raw) |
|---|---|---|
| disk facts (fast path) | PASS | PASS |
| file listing (tool use) | FAIL (no tool call) | **FAIL + FABRICATED a listing** |
| 17×23=391 | PASS | PASS |
| 60km/45min=80 | FAIL (verbose, truncated) | FAIL (same) |
| Arabic sentence | **PASS (pure Arabic)** | **FAIL ("ي moisten الهواء" — mixed EN)** |
| CPU temp honesty | PASS | PASS |
| **TOTAL** | **PASS 4 / FAIL 2** | **PASS 3 / FAIL 3** |

**The tuned 1.5B beats the raw 3B.** Raw-bigger is not automatically better:
the 3B base hallucinated content (invented a file listing — the worst failure
shape) and cannot hold pure Arabic. CONSEQUENCE: the 3B path only makes sense
TUNED — soup graduation on the 3B base — which stays BLOCKED by the disk rule
(D: 17.2%). Honest caveat: 6 probes is a smoke A/B; the 39-case exam is the
real promotion gate.

## INCIDENT found and fixed the same evening
The owner's C:/D: cleanup DELETED D:/hwk-models/Qwen2.5-1.5B-Instruct — the
base model checkpoint-3933 loads on top of. Every restore died with
`OSError: Repo id must be in the form 'repo_name'...` (loader fell back to a
HF Hub lookup for a local path). Brain was UNRECOVERABLE on restart until
re-downloaded. Fixed: snapshot_download 3.09 GB into the exact path, 3933
re-served and id-verified on :20129. LESSON: base models under hwk-models are
LOAD-BEARING — any cleanup checklist must protect them (ckpt bases, xtts-v2,
piper, faster-whisper).

## Harness + infra notes (all test-verified)
- brain_ab.py fixes: verdicts computed on the FULL reply (300-char truncation
  was failing verbose-correct answers), ERROR rows never count as PASS, Arabic
  purity = repo language-guard standard (3+ consecutive Latin letters fail),
  warm-before-score protocol (cold first generation hit the 90s timeout on
  BOTH models).
- Watchdogs: brain watchdog stopped/relaunched by protocol (verified shim+real
  pair). The SERVER watchdog fired during the swap flaps and left DUPLICATE
  :5055 listeners (Windows allowed the double-bind) + the pre-existing server
  from yesterday — converged to ONE server (health ok). Design note:
  fire_guard on every transient down-flap can stack servers.
- Final state: :5055 single+healthy, :20129 = checkpoint-3933 (id-verified),
  watchdogs alive, test_machine_facts 10/10, GPU 37°C.

## NEXT RUN PREPPED (2026-10-06 night — owner directive: "bigger and smarter")
- soup.yaml `base` switched 1.5B -> **D:/hwk-models/Qwen2.5-3B-Instruct** with
  the A/B evidence in the comment (raw 3B lost 3/6 -> tuning is the point).
  Verified: yaml parses, base dir exists, train data exists, max_length 768,
  4-bit quant. No test pins the old base path (checked tests/ for it).
- Scheduled tasks audited: HWK SoupPipeline / NightCaretaker / PiCI /
  StatusDigest all DISABLED -> nothing can auto-launch on the new config.
- 1.5B tokenizer = 3B tokenizer (same Qwen2.5 BPE), so the SFT token budgets
  in build_aali_sft_v2 stay valid for the 3B run.
- STILL GATING the launch: D: 17.2% < 20% rule (need ~13 GB) — space scan
  running detached into D:/hwk-data/_dscan.txt to name the hogs; RAM was
  2.8-3.2 GB free (the pipeline preflights handle this, but more is better).
- LAUNCH SEQUENCE when gates pass (same as prior graduations): backup
  tuned/ -> archive the old verdict -> taskkill :20129 by verified PID ->
  launch_detached --log soup_3b -- .venv/Scripts/python.exe
  scripts/soup_pipeline.py. The pipeline's own gates (exam integrity, floors,
  mix hygiene, GPU) all still apply on top.