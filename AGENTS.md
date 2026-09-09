# AGENTS.md — تعليمات لأي ذكاء اصطناعي يعمل على هذا المستودع

> This file is the contract for ANY coding agent or human contributor working
> in this repository. **Read it fully before touching anything.** If this file and your assumptions disagree, this file wins.
> Keep this file updated as the project evolves — it must always describe
> reality, not intentions.

---

## 1. ما هذا المشروع (What this project is)

**HWK Aali (آلي)** — a home-built AI system by the owner (HmamK) on a single
Windows PC (8GB-VRAM GPU, 16GB RAM, ~2TB across C:/D:/X:):

1. **نموذج لغوي يُبنى من الصفر** — a ~125M-param decoder-only transformer
   (RoPE, SwiGLU, RMSNorm) trained from scratch on the owner's own data.
2. **وكيل برمجي وأدوات** — a file/command agent (sandboxed to
   `D:\hwk-projects`) + OCR/document/video/image tools, so the model grows
   into a real assistant rather than a chat toy.
3. **عملاء متعددون لعقل واحد** — Web (Arabic-first RTL), CLI, Desktop shell,
   iOS app (SwiftUI, built on the owner's MacBook) — all on one API.

## 2. خريطة المستودع (Layout)

| Path | What it is |
|---|---|
| `file-agent/` | Flask app (`app.py`), agent loop, tool layer (`file_agent/`), model code (`hwk_model/`) |
| `file-agent/hwk_model/` | Model architecture + tokenizer wrappers (RoPE transformer, BPE loader) |
| `web/` | Arabic-first web client (static, GitHub-Pages ready) |
| `ios/Aali.xcodeproj` | Native iOS app (SwiftUI) — build on the MacBook |
| `scripts/` | One-click `.bat` runbook + tool CLIs (image gen, video, OCR) |
| `tests/` | pytest suite — MUST stay green (`run_tests.bat`) |
| `rules/`, `roadmap/`, `docs/` | Behavior rules, honest roadmaps, client docs |
| `soup.yaml` + `D:/hwk-tools/soup-venv/` | Soup (fine-tune/serve CLI): Aali's local teacher + exam comparison model; see `docs/soup_setup.md` |
| `data/` | SFT datasets (teacher Q&A, rules examples) |
| `download_corpora.py` | HF corpus downloader (resume-safe, capped) |
| `tokenize_corpus.py` | Corpus → token shards (BPE 32k) |
| `train_scratch.py` | Phase A/B trainer (resumable, mirrors to X:) |
| `mentor_a.py` … `mentor_d.py` | Mentor capture automation (external desktop/web assistants → SFT examples) |

**Data lives OUTSIDE the repo** on `D:\hwk-data\` (raw corpora, token shards,
teacher records, logs, SD/whisper models) and `X:\hwk-backups\` (mirrors).
Never commit datasets or logs into git.

## 3. القواعد — افعل / لا تفعل (Rules)

**YES — do:**
- Run `scripts\run_tests.bat` (pytest) after changing Python code; keep 18/18 green.
- Keep every file-compiling; Python 3.12; ASCII-safe imports; type hints.
- Keep the API contract stable: `POST /api/ask {"message","sid"} → {"ok","reply","sid"}` and `GET /api/health`.
- Keep new agent tools registered in `_FUNCTIONS` + `_DEFINITIONS` in
  `file-agent/file_agent/file_tools.py`, sandboxed via `_resolve()`, with tests.
- Write UI text **Arabic-first, RTL** (this is an Arabic-first product).
- Log long-running jobs to `D:\hwk-data\*.log` and never leave servers on
  random ports — the standard ports are 5055 (brain) and 5678 (n8n).
- Update this file (§5 Status) when you change the layout or finish a milestone.

**NO — never:**
- **Never kill python/node processes by guess.** Look up the exact PID owning
  the port first (`netstat -ano | grep LISTENING`). This killed a 12h job once.
- **Never run training on less than 20% free disk**, never delete checkpoints
  on `X:\hwk-backups\`, never `git push` unless the owner says so.
- Never copy text/code from leaked or proprietary sources into training data
  (the owner's rules: learn *patterns*, not stolen text).
- Never hardcode absolute user paths into library code; use `hwk_paths.py`.
- Never break the sandbox: agent writes stay in the workspace; command
  allow-list in `file_tools.py` must not be widened casually.
- No new heavy deps in the training venv (multimedia stuff goes to
  `D:\hwk-tools\sd-venv` or subprocesses).

## 4. تقسيم العمل بين الأجهزة (Multi-machine protocol)

The owner works from this PC **and** a MacBook. To let two agents work safely
in parallel:

- **This PC (heavy)**: training, tokenization, corpus downloads, GPU jobs
  (SD/whisper), long evaluations. Claims tasks from `tasks/` below.
- **MacBook / other machines (light)**: code edits, docs, UI work, iOS app,
  small scripts, tests (CPU-only paths). Never launch training/downloads.
- **Coordination**: before starting a task, create `tasks/<name>.md` with
  `owner: <machine-or-agent>`, `status: in-progress`, `started: <date>`.
  If a `tasks/*.md` already claims the same file/area, **do not start** —
  pick another or coordinate. Mark `status: done` (or `abandoned`) when finished
  and delete stale claims older than 7 days.
- **Sync**: the repo itself is the shared state. Pull before you work, commit
  small and often with clear messages. Data on D:/X: is per-machine; do not
  assume your local D: matches another machine's.

## 5. الحالة الآن (Status pointer — keep current)

- **Tokenizer**: pile (18.8B+ tokens, ~2358 shards) — Arabic next.
- **Next milestone**: Phase A pretraining launches when pile+arabic shards are
  complete (ctx 1024, d_model 768, resumable, mirrors to X:).
- **Teacher data**: 87+ SFT records captured from the external mentors in
  `data/teacher_sft.jsonl`.
- **Recent additions**: web client (`web/`), CLI (`aali_cli.py`), desktop shell
  (`desktop_app.py`), iOS project (`ios/`), OCR/doc/video tools, SD image
  gen/edit (`scripts/image_tools.py`), n8n workflow JSON, AGENTS.md.
- **Long-term memory (LIVE)**: Aali remembers the user across restarts and
  new conversations — `file-agent/file_agent/memory.py` + `memory` tool
  (save/recall/forget/summary); store `%USERPROFILE%\.aali\` (redirect:
  AALI_MEMORY_DIR); newest instruction wins per topic, contradictions surface
  ("earlier you told me X"), secrets auto-redacted, user-only statements,
  memory-forget confirmation-gated; Ollama num_ctx 8192
  (AALI_OLLAMA_NUM_CTX). Docs: docs/long_term_memory.md.
- **Security hardening (LIVE)**: lessons from real industry incidents
  (2023–2025, OWASP injection)
  taught in all four system prompts EN+AR and skills/security-rules.md,
  enforced in code: run_command blocks env-dumping (os.environ/process.env/
  printenv/$VAR/.env) and redacts credentials from tool output; Aali never
  exposes his own prompts/code/env/logs. Docs: docs/ai_security_lessons.md.
- **Next-stage automation (LIVE, waiting on GPU)**: scripts/soup_pipeline.py
  runs since 18:52 — waits for a free GPU (python-family check + log idle),
  then serves the Soup teacher, runs the 24-case baseline exam, re-SFTs on
  sft_v2.jsonl (4,086 records: capped mix + tools + mentor failures ×3 +
  live chats + memory/security episodes; dedup + exam-leak gated), grades the
  tuned model, writes the promotion verdict
  (docs/post_phaseA_pipeline.md). scripts/after_phaseA.bat still handles the
  1024→4096 context extension; launch it once too.
- **Suggestions (LIVE)**: every Aali reply now ships up to 3 safe,
  language-matched follow-up chips (/api/ask `suggestions` field; CLI shows
  numbered chips — typing 1/2/3 sends one).
- **Emoji intelligence (LIVE)**: file_agent/emoji.py reads the emotion in
  user emojis (8-emotion model, AR+EN reactions) and decorates replies with
  ≤1 fitting emoji — never on errors/refusals/security/serious topics;
  skills/emoji-etiquette.md; generate_emoji tool draws custom emoji stickers
  from scratch (Pillow, CPU, works while GPU trains).
- **Autocorrector (LIVE)**: file_agent/autocorrect.py corrects chat-speak
  and typos ("hai dode" → "hi dude") + Arabic normalization, preserves
  paths/code/quotes/numbers, shows a polite corrected-reading hint.
- **PC command library (LIVE)**: skills/pc-commands.md — files, networking,
  processes, diagnostics with read-only-first safety rules.
- **Arabic corpus (LIVE)**: fetch_arabic_corpus.py — 4,000 Wikisource dump
  pages + 105K hotel-review lines verified and downloaded;
  5,000 Arabic SFT episodes seeded; sft_v2 rebuilt at 6,108 records with
  34% Arabic share (recovery-report rebalance goal met).
- **Semantic memory (LIVE)**: recall is hybrid keyword + embedding cosine
  (Ollama nomic-embed-text, vectors cached in ~/.aali/); "deployments"
  finds the GitHub-push rule; honest `mode` field, keyword fallback.
- **Night caretaker (ON DUTY since 19:34)**: scripts/night_caretaker.py —
  watches Phase A hourly; auto-heals the mentor lab; when the GPU frees it
  audits+prunes lab episodes (scripts/audit_mentor_lab.py: runs every build,
  keeps only verified-working ones), rebuilds sft_v2, launches the soup
  pipeline, chains the 4096 extension, and writes D:/hwk-data/MORNING_REPORT.md.
- **Brain tree (LIVE, owner-only)**: /brain — admin-gated live tree of every flow (signin, signup, chat, output, keys, memory, brain selection, training) with each step citing its real code file; /api/brain/live JSON; live snapshot = counts/timestamps only, never user content. Obsidian export: scripts/build_brain_vault.py → D:/hwk-data/aali-brain-vault (wiki-linked, Graph View). Tests in tests/test_brain.py.
- **Aali as a product (2026-09-07)**: one server, every client — the vision is
  Aali on servers with users on web/desktop/CLI/terminal. Shipped: SSE
  streaming (`/api/ask/stream` with live tool-activity events via the
  agent_log bus), sessions sidebar API (`/api/sessions`, GET/DELETE
  `/api/session/<sid>`), multi-user mode (`AALI_API_KEY` → X-API-Key gate +
  per-user session isolation), desktop web app rebuilt (streaming, markdown,
  suggestion chips, sessions sidebar, voice input, stop/copy buttons —
  `web/src` now builds; dist served at `/ui/`), Dockerfile + compose for
  deployment, mock API for GPU-free UI testing (scripts/mock_aali_api.py).
  Docs: docs/desktop_app.md. Tests 46/46.
- **GPU gate for Phase B launchers (2026-09-09)**: scripts/wait_gpu_free.py is
  THE shared gate — library (card_is_safe / wait_until_safe /
  python_compute_pids / vram_used_mib / training_log_idle_minutes) + CLI
  (fail-closed, exit 2 = NO GO; logs to D:/hwk-data/gpu_gate.log) checking
  python/soup/ptxas compute processes + VRAM <1500 MiB. Appended to
  extend_context.bat before the trainer: a caller may run a stale in-memory
  copy of its own code (the 11:27 today_chain predates its 12:00 VRAM-poll
  fix) while cmd reads .bat launchers from disk at launch time — so the gate
  protects every caller, old or new. Proven live 14:24: blocked during the
  soup train, GO'd only when the card was truly idle, Phase B launched clean.
  Refactor: soup_pipeline (gpu_free/wait_for_gpu/wait_vram_clear),
  today_chain (wait_vram_clear) and night_caretaker (gpu_really_free/
  training_state) now all delegate here — no per-script nvidia-smi polling
  copies left; pipeline/caretaker keep their extra training-log-idle guard.
- **Soup smoke gate + phantom-verdict fix (2026-09-09)**: scripts/soup_probe_smoke.py
  serves an adapter checkpoint on CPU (--device cpu, port 20130, GPU untouched)
  and grades a 6-case subset (img ×2, halluc ×2, security, memory —
  SMOKE_CASE_IDS in soup_exam.py; --ids filters any run). soup_pipeline now:
  (1) runs soup train as a watched process — a watcher thread probes the newest
  checkpoint every ~6.5 min and ABORTS training at 2 consecutive failed probes
  (<3/6 pass); unavailable probes (no checkpoint yet / CPU serve down / timeout)
  never count — only a pass proves health; (2) fixes the 2026-09-09 phantom
  verdict: the tuned exam once graded port 20129 AFTER the teacher server was
  terminated — 26 empty replies = a fake 0/26 "regression"; now the tuned
  adapter is served + health-checked ("_wait_http) before grading, that same
  server STAYS UP on PROMOTE (the live brain) and is stopped otherwise;
  (3) write_verdict takes a failure reason. Also: _adapter_dir sorts checkpoints
  numerically (lexic put 10000 before 9000). Verified live: the real
  checkpoint-4326 probed 0/6 on CPU WHILE Phase B trained — the adapter
  genuinely under-learned the tool protocol (file-tool JSON appears, image-tool
  behaviors never took); teacher re-SFT needs a format-heavy mix before the
  next graduation run. Tests: tests/test_soup_smoke_gate.py (25).
- **Known gaps**: n8n webhook needs one manual activation click in the editor;
  ffmpeg installed but PATH needs refresh in new shells; web-mentor capture
  experimental; sft_v2 Arabic share rebalanced to ~34% (was 1.4%); chat
  attachments (photo/file analyze-first loop) designed but not built yet.
- **Owner brain & publish (2026-09-08/09)**: /brain live tree + /api/brain/live
  + SSE event feed (`/api/brain/events`, `/api/brain/stream`, admin-gated);
  Obsidian vault export (scripts/build_brain_vault.py, hooked into the night
  caretaker before training); admin dashboard got an 'Open Brain Tree' button
  (short-lived token handshake, master key never in the URL). Publishing:
  scripts/publish_aali.py (HF/Ollama/GGUF preflight) + scripts/aali_deploy.bat
  owner-only deploy menu + docs/publish_aali.md. UI: warm-black #252523 theme,
  gold HWK icon (web/icon.svg + make_hwk_icon.py), shareable ?sid= deep links.
  Model: Phase A complete (90k steps / 2.95B tokens); Phase B context-4096
  extension running under the night caretaker; soup graduation pipeline queued
  behind it (baseline→QLoRA→exam→auto-promote, VRAM-safe staging fixed).
  Guide: scripts/make_aali_guide.py renders Aali-Guide-<date>.pdf to the
  Desktop from docs/assets screenshots; docs/assets/aali_tour.gif in README.
- **Terminal client (2026-09-09)**: scripts/aali_cli.py — Claude Code-style
  colorful REPL (● tool traces via /api/ask/stream SSE, spinner, /new /open
  /clear /theme gold|matrix|ocean (saved in ~/.aali_cli_theme, --theme/AALI_THEME
  flag, live swatch preview) /tools (live from /api/tools) /multi /sid /help,
  y/N danger confirm, UTF-8 forced for exe use); raw-mode LineEditor:
  ↑/↓ history persisted in ~/.aali_cli_history, ←/→/Home/End editing,
  TAB completion (commands/themes/tool names), paste-safe multi-line
  (pasted newlines open continuation lines; trailing \ = typed
  continuation); /api/tools endpoint + agent_loop.tool_specs(); ships as
  build-desktop/dist/aali-cli.exe inside the installer with a Start-Menu
  «آلي — Terminal» entry (Aali-Setup.iss [Icons]); docs/assets/aali_cli_demo.gif
  + README CLI section; auto-starts the server via start_app.bat when absent.

— Last updated: 2026-09-09 (owner brain tree + event feed + vault; aali_deploy publishing menu; new theme/icon; Phase A done, Phase B 4096 running, soup graduation queued; earlier: Aali-as-a-product server + streaming + multi-user + desktop app, memory + security upgrade, Phase A restart bf16 + watchdog, OmniRoute + Soup, edit_image/edit_video + machine_ops, mentor learning loop; sft-now remains condemned — re-SFT with the rebalanced mix + mentor episodes at ≤3 epochs, promote only on the exam)
