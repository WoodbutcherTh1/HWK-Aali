# Hebrew data collection — Aali's second language (AR + HE)

- owner: buffy (this PC, Freebuff session)
- status: in-progress — PHASE 3.5 (source expansion; Sefaria first)
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
- PHASE 3.5 (owner GO 2026-10-02): expand sources toward ~5-6B tokens
  pre-cleaning — [3.5.1] Sefaria (PD+CC-BY religious corpus, THE BIG WIN),
  [3.5.2] open-access academic Hebrew, [3.5.3] permissive code comments,
  [3.5.4] Common Crawl Hebrew (CAUTION, last), [3.5.5] hewiktionary
  re-eval on shortfall. License verified FIRST per source; checksums
  mandatory; NO cleaning/tokenization yet; report after EACH source.
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
- 2026-10-02 PHASE 3.5 IN PROGRESS (owner GO received; sources expanded):
  [3.5.1] SEFARIA — the repo was restructured (Sept 2026) into a
  lightweight index (books.json: 19,754 entries / 13,548 Hebrew) plus the
  public GCS bucket gs://sefaria-export/ (~26 GB of texts; verified
  live). LICENSE.md: NO overall license — per-text `license` field. EVERY
  Hebrew entry was Range-probed (first 8 KB — the license field sits in
  the metadata header, verified on real files) BEFORE any download:
  allowed 6,005 rows (Public Domain 5,292 + PD 140 + CC-BY 295 +
  CC-BY-SA 246 + CC0 32); excluded 6,257 with no license (6,214 merged
  composites — merged files carry no license — + 43 specific versions),
  931 "unknown", 354 CC-BY-NC, 1 probe failure. Excluded files were
  NEVER downloaded. Fetch: one best-licensed version per title → 5,423
  files, 1.90 GB, per-file sha256 (raw/sefaria/manifest.jsonl), 1
  unsafe-path skip, 0 download errors. Extraction →
  extracted/sefaria/sefaria_versions.jsonl. Script:
  scripts/fetch_sefaria.py (probe/fetch/extract; recon left in
  scripts/_probe_sefaria.py).
  [3.5.2] ACADEMIC — the win is the KNESSET CORPUS
  (huggingface.co/datasets/HaifaCLGroup/KnessetCorpus, CC-BY-SA-4.0
  verified live on the card; University of Haifa/IAHLT/TAU-Yaffo; ~35M
  sentences / 384M+ tokens of 1992-2024 parliamentary Hebrew): 120
  no-morph sentence shards (plenary+committee), 3.67 GB, ALL
  sha256-verified vs the LFS oid (raw/knesset/manifest.jsonl). Extraction
  keeps ONLY sentence_text + public protocol metadata — NO speaker
  names/genders/factions (privacy-first) — and skips is_ocr_output rows
  (paper documents heavy OCR errors). Script: scripts/fetch_knesset.py.
  Dead-ends documented: arXiv (no Hebrew full-text mass), TAU/HUJI/
  Technion repositories (no license-clean bulk path; TAU library terms
  prohibit systematic downloading), HeSum/HebDB/IsraParlTweet (news /
  speech / tweets — rejected). PENDING OWNER: HPLT v2 Hebrew gated
  (HTTP 401 without accepting terms), fineweb-2 heb_Hebr license =
  odc-by (not in the owner's allowed list); raw Common Crawl NOT started
  (riskiest, owner said do it last). [3.5.3] code comments: needs a
  GitHub token (API auth) — awaiting owner decision. [3.5.5]
  hewiktionary: still deferred to the Phase 5 shortfall check.
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
