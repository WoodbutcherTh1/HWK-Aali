# Hebrew data collection — Aali's second language (AR + HE)

- owner: buffy (this PC, Freebuff session)
- status: in-progress — PHASE 1 (setup + license audit)
- started: 2026-10-01
- owner GO: "APPROVED: Hebrew Phase 1 — on my go. Do Phase 1 only"
  (2026-10-01). WAIT after each phase; nothing enters raw/ without a
  verified license; no downloads in Phase 1.

## Mission
Add Hebrew as Aali's second language: clean, licensed, safe corpora →
tokenized shards → Phase D continuation + future SFT. Target 5–10B tokens
(match Arabic's QUALITY, not size). All processing local, CPU-only.

## Non-negotiable rules (from the owner's brief)
1. LICENSE-FIRST: CC0/CC-BY/CC-BY-SA/MIT/Apache-2.0/PD only. Unclear =
   skip. Never paywalled/leaked/scraped-against-ToS.
2. SAFETY-FIRST: no personal data, private conversations, medical/legal
   records, private forums, anything behind a login.
3. QUALITY-FIRST: Wikipedia/Wikisource > academic OA > PD books > open
   news > permissive code.
4. PROVENANCE: every source logged (URL, license, date, size, checksum)
   in D:/hwk-data/hebrew/SOURCES.md.

## Phases (owner gates each one)
- PHASE 1 (now): dirs, live license verification, tokenizer decision,
  niqqud stance, doc skeletons.
- PHASE 2: Hebrew Wikipedia dump (download + extract) — after owner go.
- PHASE 3: Wikisource, Wiktionary, Ben-Yehuda, PD books.
- PHASE 4: clean + dedup (MinHash/near-dup, Unicode NFC, unniqqud).
- PHASE 5: quality filter (language detect, length, toxicity).
- PHASE 6: tokenize (per TOKENIZER_DECISION.md) → D:/hwk-data/tokens/hebrew/.
- PHASE 7: SOURCES/LICENSE_REPORT/STATS/CLEANING_LOG final docs.

## Phase 1 status log
- 2026-10-01: tokenizer probe DONE (pre-approval diagnostic): hwk_spm BPE
  32k on real Hebrew sample = 52 chars → 23 tokens, unk rate 43%
  (AR 0%, EN 0% on same probe) — Hebrew is near-unusable without a vocab
  decision. Measurement method + recommendation in TOKENIZER_DECISION.md.
- 2026-10-01: phase-d-8k confirmed FINISHED (final.pt + checkpoint.pt +
  trainer-state.pt on D:/hwk-models/phase-d-8k/) — Hebrew becomes a
  natural stage-6 continuation corpus when tokenized.
- 2026-10-01 PHASE 3 COMPLETE (owner GO received): hewikisource-20261001
  downloaded (1 part, 463.9 MB, SHA1-verified vs dumpstatus manifest) +
  extracted: 243,261 docs, 0.812 GB chars, 75.8% Hebrew, ~203M tokens.
  Ben-Yehuda fetched from the RIGHTS-HOLDER's own GitHub dump repo
  (LICENSE = public domain, credit requested; the HF dataset is just a
  loader script streaming the same txt files) via git clone --depth 1
  --filter=blob:limit=400k (git fsck exit 0 = all objects SHA1-verified);
  extracted 26,455 works, 0.472 GB chars, 76.8% Hebrew, ~118M tokens.
  DECISIONS: hewiktionary SKIPPED (thin prose, revisit on token
  shortfall); hewikinews SKIPPED (owner-approved; no dated dumps).
  CUMULATIVE pre-cleaning: 675,581 docs, 2.40 GB text, ~600M tokens.
  LESSONS: single-part wikis have NO part index in the filename (extractor
  glob fixed to *pages-articles*.bz2); GitHub raw 404s on guessed zip
  paths — verify repo tree via API before assuming a bundle exists; long
  CPU loops never run in the foreground console (timeout killed one
  Ben-Yehuda pass mid-run — relaunched detached per house convention).
  fetch_benyehuda.py kept as a documented dead-end (zip path 404s; clone
  is the working path).
- 2026-10-01 PHASE 2 COMPLETE (owner GO received): hewiki-20261001 dump
  downloaded as 6 parts (1.151 GB), every part SHA1-verified against the
  official dumpstatus.json manifest — scripts/fetch_hebrew_wiki.py
  (resume-safe: Range + partial continuation + manifest copy +
  content-free DOWNLOAD_LOG.md; Wikimedia 403s the default python UA —
  identified UA per their policy). Extracted with wikiextractor 3.1.0 in
  D:/hwk-tools/hebrew-venv (new CPU-only venv) via
  scripts/extract_hebrew_wiki.py (per-part resumable, markers): ALL 6
  parts in ~13 min (much faster than the 2–4h estimate). Quick stats
  (scripts/hebrew_corpus_stats.py): 405,865 articles, 1.117 GB text
  chars, Hebrew-script share 75.5% (HEBREW-OK), rough estimate ~279M
  tokens (chars/4). No cleaning (Phase 4), no tokenization (Phase 6).
  NOTE: hewiktionary/hewikinews checked — hewikinews has NO dated dumps
  on 20261001 (skip; tiny wiki), hewiktionary available if wanted in
  Phase 3. Provenance rows filled in SOURCES.md; STATS.md has the Phase 2
  block.
- 2026-10-01 PHASE 1 COMPLETE: dirs created; LIVE license audit done
  (dumps.wikimedia.org indexes live for all 4 wikis; he.wikipedia.org
  footer = CC BY-SA; Ben-Yehuda site live with per-author rights markers
  and its HF mirror projectbenyehuda/hebrew_projectbenyehuda tagged MIT;
  HebrewBooks = BLOCKED, no license statement; IA = conditional per-item;
  HF per-dataset verdicts recorded). TOKENIZER_DECISION.md: Option A
  extend 32k→40k (measured 43% unk; niqqud silently dropped by tokenizer;
  dual-tokenizer forfeits bilingual transfer; retrain invalidates
  phase-d-8k). NIQOUD_STANCE.md: unniqqud, strip U+0591–05C7 post-NFC,
  keep sofit + maqaf + gershayim. SOURCES.md + STATS.md skeletons in
  place. NOTHING downloaded — raw/ is empty by design.
