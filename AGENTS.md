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
   (RoPE positional embeddings, LayerNorm, GELU MLP) trained from scratch on
   the owner's own data.
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
- **Graduation pipeline (LIVE, waiting on GPU)**: scripts/soup_pipeline.py —
  waits for a free GPU via the shared gate (python-family check + log idle),
  then serves the Soup teacher, runs the 24-case baseline exam, re-SFTs on
  sft_v2.jsonl (6,166 records, 35.5% Arabic — see the 2026-09-10 audit bullet
  below; dedup + exam-leak gated), grades the tuned model, writes the
  promotion verdict (docs/post_phaseA_pipeline.md). Phase B owns the card
  until it finishes (~21:00 on 2026-09-10); the modernized night caretaker
  fires the pipeline the moment the GPU frees. No extension chaining —
  Phase B IS the 4096 extension and it is already running.
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
  5,000 Arabic SFT episodes seeded; sft_v2 now 6,166 records with 35.5%
  Arabic share (2026-09-10 audit: mentor data actually present — see the
  audit bullet below).
- **Semantic memory (LIVE)**: recall is hybrid keyword + embedding cosine
  (Ollama nomic-embed-text, vectors cached in ~/.aali/); "deployments"
  finds the GitHub-push rule; honest `mode` field, keyword fallback.
- **Night caretaker (RELAUNCHED 2026-09-10, modernized)**:
  scripts/night_caretaker.py — watches Phase B (D:/hwk-data/context_training.log)
  every 10 min until the GPU frees, then: audits+prunes lab episodes
  (scripts/audit_mentor_lab.py: keeps only verified-working builds), rebuilds
  sft_v2 + soup export, launches soup_pipeline.py, waits for its verdict, and
  writes D:/hwk-data/MORNING_REPORT.md. Phase A-era behavior REMOVED: no
  Phase A resume (it is DONE — a resume would fork Phase B's lineage) and no
  4096-extension chaining (double-launch would collide with the pipeline on
  the 8GB card). Duty window extended to 28h so the whole Phase B →
  graduation arc fits inside one caretaker lifetime (2026-09-11: window
  bumped again after the power-off crash delayed the finish). Trainer,
  caretaker and pipeline survive Freebuff restarts (WMI-spawned) but NOT an
  OS power-off/reboot — relaunch order: scripts/extend_context.bat, then
  night_caretaker.py.
- **Pi-CI (2026-09-11, scheduled task HWK PiCI)**: the Raspberry Pi
  (192.168.1.9, user `aalici`) is Aali's permanent ARM/Linux test target.
  Every 15 min the PC ships a git bundle (no GitHub credentials involved) via
  D:/hwk-data/pi_ci/ship_to_pi.sh; the Pi clones/updates, installs deps and
  runs the full pytest suite (1h timeout), and the PC fetches result.json →
  D:/hwk-data/pi_ci/. One-time setup pending: see D:/hwk-data/pi_ci/README.md
  (two ssh commands as pi@, then the task fully takes over). Pi is off →
  cycle skips silently.
- **Status board (2026-09-11, scheduled task HWK StatusDigest)**:
  scripts/status_digest.py regenerates D:/hwk-data/STATUS.md every 30 min —
  alerts-first (stalled trainer/caretaker, NO-GO verdict, disk rule, Pi-CI
  failures), then Phase B step/pace/ETA (computed across consecutive runs,
  never from the trainer's own ETA column), caretaker, graduation pipeline
  (verdict read from the report FILE's freshness, not old log lines), Pi-CI,
  disks. Run by hand anytime: python scripts/status_digest.py
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
- **Chat guards: no more "{}", echo, or language drift (2026-09-09)**: live
  owner transcript showed the 7b brain answering with literal "{}" (shapeless
  JSON surfaced raw), parroting the user's message back, and answering
  Arabic to English. agent_loop.py now: _parse_local_model_response returns
  shell-JSON as empty text (content-candidate kept, real protocol object
  always wins) and the loop RETRIES twice with feedback, then degrades to an
  honest apology — raw JSON can never reach the user; _is_echo_of_user()
  rejects verbatim parrots (normalized containment + <50% extra) once with
  feedback; the language guard NAMES the target language (الإنجليزية
  فقط/العربية فقط) — "same language as the user" was too abstract for 7b.
  Verified live against the running brain (EN→EN, AR→AR). Tests:
  tests/test_agent_guards.py (11).
- **Desktop auto-boot (2026-09-09, v1.0.2)**: owner request — launching
  آلي Desktop must start Aali itself. aali_desktop_app.py::_ensure_local_server
  (daemon thread before the window) runs scripts/start_all.bat hidden when a
  LOCAL server is down: repo discovery via AALI_HOME → %APPDATA%\AaliDesktop\
  repo.txt → cwd/exe dir → HWK-Aali* under home/Desktop/Documents/C:/D:,
  found path remembered; waits up to 120s for /api/health (inject deadline
  90s). start_all.bat honors AALI_NO_BROWSER (no duplicate tab) and skips
  n8n when 5678 already LISTENING (no duplicate workers). Installed exes
  updated in place; installer rebuilt (Aali-Desktop-Setup.exe 1.0.2).
  Proven live: server down → installed app launch → up in ~4s.
- **Sharing hardening (2026-09-09)**: friends-as-users done safely.
  (1) Fail-safe bind: an unauthenticated server (no AALI_API_KEY) binds
  127.0.0.1 only — it can never silently face the LAN again (was always
  0.0.0.0); key mode binds 0.0.0.0; AALI_BIND overrides.
  (2) Server-enforced guest policy: remote non-admin keys (loopback + no
  X-Forwarded-For = local; spoof-proof) are forced to policy=guest in both
  /api/ask and /api/ask/stream — run_command/machine_ops/delete_file/
  move_file/memory hard-blocked with guest_forbidden regardless of
  client-sent policy/confirm (confirm is client-supplied, NOT a security
  boundary; cloudflared sets X-Forwarded-For so tunnel guests are gated).
  (3) scripts/aali_share.bat (owner-only): generates the master key to
  D:/hwk-data/aali_master_key.txt, adds a LAN-only (localsubnet, private
  profile) firewall rule for 5055, restarts in multi-user mode with
  HWK_ALLOW_COMMANDS=0. (4) aali_tunnel.bat now REFUSES to expose an
  unauthenticated server (verifies key mode via /api/health first).
  (5) scripts/aali_domain.bat (owner-only, NEW): permanent named-tunnel
  on your own domain (aali.dpdns.org via DigitalPlat FreeDomain + a free
  Cloudflare zone) — same key-mode gate as aali_tunnel.bat, one-time
  `tunnel login` (browser Authorize), creates the named tunnel `aali`,
  writes config.yml (hostname -> http://127.0.0.1:5055), routes DNS, and
  runs `tunnel run`. HTTPS automatic, no router ports, PC IP hidden.
  Prereq (browser steps, per docs/custom_domain.md): add the zone to
  Cloudflare, set Cloudflare's nameservers as the domain's external
  nameservers in the DigitalPlat dashboard, wait for propagation.
- **Attachments (LIVE, 2026-09-09)**: the "designed but not built" gap is
  closed — 📎 Attach button in the web UI (native picker: PC folders on
  desktop, phone files/camera on mobile), up to 4 files, 100MB cap, any mix
  of images / mp4+videos / audio / pdf+docx+xlsx / text+code. Server:
  POST /api/attach saves into uploads/ inside the sandboxed workspace
  (name sanitized, traversal-proof) and ANALYZES IMMEDIATELY (analyze-first:
  Windows OCR for images, doc parser for documents, Whisper transcript +
  frame OCR for video/audio) so the ask never waits on analysis; the client
  sends attachments:[stored names] and the ask routes re-validate + load
  fresh analysis server-side (client analysis never trusted), appending an
  Arabic context block with the extracted text + workspace-relative path to
  the message — Aali answers from real content. UI: upload chips with image
  thumbnails, image previews in the user bubble (served via /api/file),
  send enabled with files-only (no text). Verified live: recipe.txt Q&A and
  PNG OCR against the running brain.- **Known gaps**: n8n webhook needs one manual activation click in the editor;
  ffmpeg installed but PATH needs refresh in new shells; web-mentor capture
  experimental; sft_v2 Arabic share rebalanced to ~34% (was 1.4%).
- **Owner brain & publish (2026-09-08/09)**: /brain live tree + /api/brain/live
  + SSE event feed (`/api/brain/events`, `/api/brain/stream`, admin-gated);
  Obsidian vault export (scripts/build_brain_vault.py, hooked into the night
  caretaker before training); admin dashboard got an 'Open Brain Tree' button
  (short-lived token handshake, master key never in the URL). Publishing:
  scripts/publish_aali.py (HF/Ollama/GGUF preflight) + scripts/aali_deploy.bat
  owner-only deploy menu + docs/publish_aali.md. UI: warm-black #252523 theme,
  gold HWK icon (web/icon.svg + make_hwk_icon.py), shareable ?sid= deep links.
  Model: Phase A complete (90k steps / 2.95B tokens); Phase B context-4096
  extension was accidentally closed by the owner at step 42,475 (20:08,
  ~7.7h left) and relaunched the same evening via extend_context.bat
  (resumes from trainer-state; GPU gate holds it until the card frees);
  soup graduation pipeline queued behind it (baseline→QLoRA→exam→
  auto-promote, VRAM-safe staging fixed).
  Guide: scripts/make_aali_guide.py renders Aali-Guide-<date>.pdf to the
  Desktop from docs/assets screenshots; docs/assets/aali_tour.gif in README.
- **Web client v2 (2026-09-09)**: dark/sepia theme switch (html[data-theme],
  CSS vars, localStorage), time-aware Arabic greeting empty-state
  (صباح/نهارك/مساء الخير), markdown tables, language-colored code fences
  (GitHub palette dot), plus the earlier streaming/sessions/chips work.
  web/src builds clean (tsc + vite); dist rebuilt and served at /ui/.
- **Terminal client (2026-09-09)**: scripts/aali_cli.py — pro-CLI-style
  colorful REPL (● tool traces via /api/ask/stream SSE, spinner, /new /open
  /clear /theme gold|matrix|ocean (saved in ~/.aali_cli_theme, --theme/AALI_THEME
  flag, live swatch preview) /tools (live from /api/tools) /multi /sid /help,
  y/N danger confirm, UTF-8 forced for exe use); raw-mode LineEditor:
  ↑/↓ history persisted in ~/.aali_cli_history, ←/→/Home/End editing,
  TAB completion (commands/themes/tool names), paste-safe multi-line
  (pasted newlines open continuation lines; trailing \ = typed
  continuation); /api/tools endpoint + agent_loop.tool_specs(); ships as
  build-desktop/dist/aali-cli.exe inside the installer with a Start-Menu
  «آلي — Terminal» entry (Aali-Setup.iss [Icons]);  docs/assets/aali_cli_demo.gif
  + README CLI section; auto-starts the server via start_app.bat when absent.
- **CLI Arabic display fix / bidi (2026-09-09)**: classic conhost has no bidi
  or shaping — Arabic printed disconnected+mirrored (owner screenshot:
  "آلي — مساعدك المحلي" → "يلآ .زهاج ،يلحلا ديسم"). scripts/aali_cli.py now
  ships stdlib-only shape_arabic (contextual presentation forms, lam-alef
  ligatures, harakat pass-through) + reorder_visual (RTL runs reversed with
  marks kept attached, brackets mirrored, latin/digits/URLs kept LTR) behind
  fx() — idempotent (presentation forms ⇒ passthrough), applied at paint() +
  render_reply + /open bodies; auto-detects bidi-capable terminals
  (WT_SESSION/ConEmu/ANSION/vscode/mintty…) so it never double-reverses;
  /bidi on|off|auto (+ --bidi/AALI_BIDI) persisted in ~/.aali_cli_bidi.
  Tests: tests/test_cli_bidi.py (18).
- **CLI bidi v2 (2026-09-09)**: two upgrades from the owner. (1) Width-aware
  fx: overlong lines wrap at the terminal width (word-aware, in LOGICAL
  order) BEFORE shape+reorder, so terminal-wrapped Arabic stays readable —
  fx(text, width) with paint/render_reply/say passing _term_width();
  (2) extended Arabic-script letters (Persian/Urdu پ چ ژ ک گ ی …) derived
  from Unicode decomposition tags at import time — typo-proof, complete,
  core Arabic table stays canonical; reh-family classified right-joining.
  v2 fix (2026-09-10, owner screenshot): _terminal_bidi_capable trusted
  WT_SESSION as bidi-capable, so the CLI skipped its transform in Windows
  Terminal — which actually renders Arabic disconnected+mirrored. Now
  platform-aware: on Windows the CLI always shapes itself (WT/conhost/
  xterm.js are the same renderer class; ConEmu + mintty/WezTerm/iTerm keep
  an explicit pass), macOS/Linux terminals are trusted (CoreText/HarfBuzz).
  Tests: test_cli_bidi.py (windows-never-trusted, non-windows-trusted,
  known-good-on-windows).- **soup-train root-cause fix: token budgets + row structure (2026-09-12
  night)**: three graduation attempts (23:48, 00:27, 01:00 UTC) died in the
  same place — `soup train` ValueError "no causal-loss target remains after
  tokenization/truncation at data.max_length". Root causes were TWO builder
  defects, found by running SOUP'S OWN code path on CPU
  (soup_cli.data.loader.load_dataset + sft_format.build_format_row +
  loss_mask.ensure_causal_loss_target — replicate before theorizing):
  (1) Arabic costs ~1.5 chars/token on the Qwen2.5 tokenizer vs ~4.0 for
  English, so the flat MAX_TOTAL_CHARS=2200 cap let Arabic-heavy mentor
  prompts reach ~957 tokens and fill the 768 window (answer truncated away
  = nothing to learn). Fix: token budgets in build_aali_sft_v2.py —
  TRAIN_MAX_LENGTH=768 (KEEP IN SYNC with soup.yaml; 1024 was tried and
  CUDA-OOM'd the 8GB card at batch_size=1), PROMPT_TOKEN_BUDGET=550,
  ANSWER_TOKEN_BUDGET=200, a per-row Arabic/English token estimator
  (_est_tokens, measured constants), row_fits/token_report, trim_to_budget
  now derives a per-row char budget from them, truncates a long task head
  but NEVER truncates the answer (untrimmable rows are dropped and named),
  and the write gate enforces it all.
  (2) conversation logs were sanitized BEFORE chunking: dropped assistant
  turns merged user turns into all-user chunks and sliced mid-exchange —
  3 user-only + 20 user-final rows reached the file and soup rejects rows
  whose assistant mask is empty (no causal-loss target). Fix: chunk first,
  sanitize per chunk, require the last turn to be an assistant turn
  (chunks_without_assistant_target stat; 40 chunks dropped on rebuild).
  Defense in depth: soup_pipeline.run_training now runs a pre-train
  dataset gate (build_aali_sft_v2.token_overflow_rows: token overflow AND
  structural checks, rows named in the log, fails in 1s instead of a
  20-min serve+exam+train cycle). Verification chain: 237 pytest tests
  green; real-tokenizer scan = 0 would-be-rejected rows; soup-path replica
  = 0 rejected. Dataset: 5,336 rows, 34.4% Arabic. Live proof: train
  finally PAST all previous failure points (attempt 6, 02:27 UTC, steps
  ticking). Tests: tests/test_sft_v2_builder.py (18, incl. chunk-boundary
  regression + live-dataset canary).
- **Smoke-gate calibration (2026-09-12 night, attempt 6 lesson)**: the
  mid-train smoke probe aborted a HEALTHY run at 30% — two graded 0/6
  probes, but the probe JSON showed real text + valid tool JSON (a 1.5B
  student learns the protocol shape long before the behaviors; the
  promoted 09-09 adapter would fail the same mid-train bar, and on 09-09
  the gate was CPU-disabled so it never once saw a passing adapter).
  soup_pipeline.smoke_watcher now aborts ONLY on proven breakage
  (_probe_is_broken: all-but-one generation EMPTY — the 09-09 phantom
  signature); low-but-real scores log as telemetry. The full 26-case tuned
  exam still decides promotion. Tests: tests/test_soup_smoke_gate.py (13,
  rewritten to the calibrated contract). Attempt 7 (03:17 UTC) relaunched
  with the calibrated gate.
- **Smoke-probe RAM gate (2026-09-12 morning, attempt 7 lesson)**: every
  mid-train probe after 08:02 died `exit 3` ("probe server never became
  ready on :20130") — the GPU trainer's host-side RAM growth left ~0.3 GB
  of 16 GB free, so the CPU serve pagefile-thrashed forever instead of
  loading Qwen-1.5B (the first probe ran before the trainer bloated).
  Training was never at risk (unavailable probes are telemetry-only).
  Fix: soup_probe_smoke.py pre-flights free RAM (SMOKE_MIN_FREE_RAM_GB=2.0,
  psutil → Win32 ctypes fallback, fail-open on unknown) and exits 4 with
  the number; soup_pipeline.run_smoke_probe maps exit 4 to a logged
  "skipped: not enough free RAM" that never counts toward the abort
  threshold, while a real exit 3 still does. Diagnosability: the probe's
  server output is captured to D:/hwk-data/smoke_server.log (was DEVNULL —
  the real error sat in a pipe nobody read) and exit 3 dumps its tail.
  Live capture proved it: the thrashing server froze at "Loading weights:
  0%" in the log. SECOND lesson the same morning: the 12:20 cycle died
  0xC000013A (console Ctrl+C/close) and the event swept the ENTIRE visible
  console — probe, trainer and pipeline all killed at step 1270/3804 (the
  pipeline had been running in the owner's terminal, NOT detached). Fix:
  the probe and its server spawn with CREATE_NO_WINDOW (no console to
  receive close events) and the pipeline logs a per-cycle "launching
  against checkpoint-<n>" marker. The run was relaunched detached via
  Start-Process -WindowStyle Hidden + stdout/stderr redirects (the
  caretaker's launch convention) — long GPU jobs must never live in a
  visible console. Tests: tests/test_soup_smoke_gate.py (37).
  Detached-launch convention now enforced in code: scripts/launch_detached.py
  (generic no-console launcher, append-mode logs under D:/hwk-data),
  soup_pipeline.self_detach() (re-spawns itself detached on first entry —
  env marker HWK_SOUP_PIPELINE_DETACHED, --no-wait preserved; --dry-run
  exempt), and the trainer .bat launchers (extend_context,
  resume_training, sft_training, aali_deploy's train menu) all route
  through them — closing the terminal can no longer kill a run.
  Integration markers: launch_detached sets HWK_DETACHED for its children
  and self_detach honors it (no double fork); HWK_NO_SELF_DETACH lets
  today_chain.py run the pipeline IN-PLACE as its serial stage (it waits
  on the exit code), with today_chain.bat detaching the whole chain.
  status_digest false alarms fixed: a caretaker log ending in "going to
  sleep" is a completed duty (not STALLED); the pipeline reads RUNNING
  from the newest UTC-stamped pipeline line (pytest junk filtered; test
  runs can neither fake activity nor fool the board); INCOMPLETE verdicts
  name the auto-retry. Tests: tests/test_launch_detached.py (10),
  tests/test_status_digest.py (10).
- **CRITICAL: dropped tuned exam restored + lock leak fixed (2026-09-12
  afternoon)**: git pickaxe shows the 09-09 smoke-gate rewrite (1d32456)
  silently dropped `tuned = run_exam("tuned", ...)` from soup_pipeline.main
  — after hours of training the pipeline reached
  write_verdict(baseline, tuned) with `tuned` never assigned: NameError,
  leaked adapter server, NO verdict. Found while reviewing the LIVE run
  (which had imported the pre-fix code and would have crashed in ~1h).
  Restored the stage (with a failure reason on exam failure). Also: the
  singleton-lock cleanup sat after a return and NEVER ran — every run left
  hwk_soup_pipeline.lock behind (a recycled python PID could block future
  launches); _release_lock() now covers every main() exit path, and a
  duplicated dead --dry-run block was removed. Salvage:
  scripts/finish_pipeline.py (launched detached, soup_finish.log) watched
  the live run and completes its missing tail — waits for trainer-done
  (gate + log idle + lock PID dead), reuses the leaked :20129 server or
  serves the newest adapter, runs the SAME exam + verdict via the
  pipeline's own functions, kill-by-port-owner (never by image name),
  idempotent behind a fresh-verdict check. Tests: tests/test_finish_pipeline.py
  (10) + a source-level regression test asserting main() grades the tuned
  adapter before the verdict.
- **Babysitter for the LIVE pre-fix run (2026-09-12 afternoon)**: the
  running pipeline could not be patched in-memory, and its smoke cycles
  kept starving the trainer (host RAM growth is unbounded; a cycle drove
  the box to 0.13 GB free) — plus a starved probe can grade empty
  generations and 2/2 would abort a HEALTHY run. scripts/finish_babysit.py
  (detached, soup_babysit.log) kills each cycle as it spawns: probe
  pythons FIRST (ungraded = unavailable, never counts toward the abort),
  then the cycle's CPU server by exact cmdline pair (serve + :20130) and
  netstat PID — never by image name, never the exam server (:20129) or
  the trainer (pinned by tests/test_finish_babysit.py, 8). Probe server
  also runs at BELOW_NORMAL priority and the RAM gate is 3.5 GB (the
  2.0 gate launched a serve that starved the box; the serve itself needs
  3-4 GB). NOTE for next runs: these two helpers exist ONLY because the
  live run imported pre-fix code — the fixed pipeline needs neither.
- **GRADUATED via salvage (2026-09-12 evening)**: the reboot killed attempt 7 at 77% trained, but tuned/checkpoint-2900 survived with a valid adapter — finish_pipeline.py salvaged the tail: served checkpoint-2900, ran the SAME 26-case tuned exam, **4/26 vs the baseline's 1/26 → VERDICT: PROMOTE** (promoted.json written, the adapter now serves LIVE on :20129 as Aali's brain; salvage log: soup_salvage.log). The 77% checkpoint beat the baseline on memory/security/anti-hallucination behaviors — 23% more training remains available if a future graduation wants a stronger adapter (full pipeline relaunch would retrain from scratch). LESSON that bit the tests: after ANY promote, promoted.json exists and agent_loop reroutes mode="local" to the promoted server — the 4 agent_loop unit tests in test_hwk.py now pin AALI_OWN_MODEL=0 (the documented kill-switch, alongside their AALI_OLLAMA=0) so they stay hermetic on every machine, including Pi CI where a promote may or may not have happened.
- **sft_v3 media-mix IMPLEMENTED (2026-09-12 night, offline steps 1–3 done; launch awaits owner go)**: tasks/sft-v3-image-tool-mix.md — why every media exam case failed (0/5): the trained file had 7 media rows in 5,336 (0.13%), and 6 of the 13 authored media episodes were killed by the exam-leak gate because they REUSED the exam's exact user prompts ("Draw me a picture of a mountain lake at dawn" IS img_basic_en). DONE: 67 leak-free episodes (image ×19, video ×12 incl. two-turn refusal→consent→call pairs, read/edit_image, emoji ×6 with the real `prompt` key, error-recovery ×8); builder HARD GATE `MEDIA_FLOORS` fails exit-2 below per-tool floors (gen_img ≥12, gen_vid ≥8, edit ≥6, read ≥6, emoji ≥4), measured on WRITTEN rows, and soup_pipeline.run_training re-checks the file before training so a stale dataset fails in one second. Rebuild verified: 5,391 records, media 7→67 (22/16/10/11/8), arabic 0.345, 0 overflow, 0 leaks, soup.yaml replica ok, backup at sft_v2.pre_sft_v3_backup.jsonl, report at sft_v3_build_report.json, suite 331. LAUNCH (one command, needs explicit go — checkpoint-2900 is promoted/live): scripts\launch_detached.py --log soup_v3 -- .venv/Scripts/python.exe scripts/soup_pipeline.py
- **Mission clock (2026-09-12 evening, owner request)**: scripts/mission_clock.py — a colorful watch-party dashboard for staring at long jobs: a big animated 5-row block clock (blinking colons, rainbow hue drift, 🎉 AALI GRADUATED banner after a promote), one card per pending process (live brain ports via /v1/models probe, graduation verdict, soup trainer with progress + THE countdown from the trainer's own tqdm ETA, exam case progress [n/26], Phase B, caretaker), state-colored (run/wait/done/stall/idle), a ⏰ NEXT EVENT line naming what finishes soonest. Every number is parsed from the real D:/hwk-data logs — nothing guessed; --plain strips ALL ANSI (strip runs over the finished frame); --once renders a single frame; --data-dir retargets; brain probe is injectable so tests never touch the network. Launch: scripts\mission_clock.bat (or the .bat with flags). GPU CARD (2026-09-12 night): parse_nvidia_smi/probe_gpu/gpu_card render the 8GB card as its own card — VRAM bar (GB used/total), state from utilization (≥20% = 🔥 cooking/run; <20% util but ≥80% VRAM = model resident/wait, the promoted server's resting state; else idle), temp as extra with a 🥵 at ≥85°C; nvidia-smi row parsed split-from-the-right so comma GPU names survive; probe injectable, missing nvidia-smi (Pi CI) simply renders no card. Tests: tests/test_mission_clock.py (32, incl. real tqdm/stage/nvidia-smi samples + plain-frame guarantee). NOTE: run through the .bat/venv python — UTF-8 is forced on the streams (cp1252 pipes choke on block glyphs). pytest.ini (2026-09-12 night): testpaths=tests + pythonpath=file-agent scripts bakes the run_tests.bat contract in — bare `pytest` works everywhere now (324 passed), including Pi CI.
- **Reboot kill + job-object breakaway (2026-09-12 evening)**: the machine
  REBOOTED at 16:28:39 local (WMI LastBootUpTime), killing attempt 7 at step
  2927/3804 (adapter served for its tuned exam — checkpoints through 2900
  survived with valid adapter_model.safetensors; the baseline exam had
  already graded 1/26). The scheduled task "HWK SoupPipeline" relaunched the
  pipeline (16:29:01) and it died within its first minute: the task's python
  exits 0 BY DESIGN (self_detach), Task Scheduler closes its JOB OBJECT
  (kill-on-job-close), and the kernel killed the "detached" child that had
  inherited the job — DETACHED_PROCESS protects against console close, NOT
  job close; only "starting Soup server" got logged. Fix:
  launch_detached.detached_creationflags() adds CREATE_BREAKAWAY_FROM_JOB
  (child leaves the job at birth; when the job refuses breakaway, fall back
  to the plain flags so the launch still works) and both spawn sites route
  through it (launch_detached.spawn_detached with an OSError fallback,
  soup_pipeline.self_detach likewise). status_digest second lesson: it
  reported "pipeline RUNNING" 34 min after everything died — a fresh start
  marker is not proof of life; RUNNING now requires recent pipeline WRITES
  (≤15 min), a fresh start marker over a silent log alerts "STALLED -
  relaunch soup_pipeline.py". Tests: tests/test_launch_detached.py (15),
  tests/test_status_digest.py (12); 292 green full-suite.
- **UI professional polish layer (2026-09-12 night)**: web/src/styles.css
  gained a refinement pass at the end of the file — antialiased type +
  text-wrap pretty, real glass sidebar/chat-head (color-mix + backdrop
  blur), layered shadows + inset hairlines on surfaces, hairline hover
  scrollbars, calmer focus rings, faster settle animations (0.13–0.18s,
  prefers-reduced-motion honored), uniform quick-pill icon boxes, calmer
  top-bar CTAs. Theme-variable driven, so sepia inherits everything.
  Rebuilt web/dist; visually verified via screenshots at /ui/.
- **sft_v2 audit + builder fixes (2026-09-10)**: the pre-graduation audit
  found the mix was NOT ready — 6,070 records, 34.6% Arabic, and ALL 67
  mentor episodes silently dropped at write time (each exceeded
  MAX_TOTAL_CHARS=2200; the write loop discarded over-length rows without a
  trace, so the documented "mentor failures ×3" never trained). Also found:
  the ×3 upweight copies were byte-identical so the dedup gate killed #2/#3
  before writing; mentor transcripts leaked the mentors' own toolset
  (Read/Edit/Bash/Grep/Write, with stringified arguments) and near-miss tool
  JSON; chat logs carried bare {"content": ...} and "{}" replies. Builder
  fixes (scripts/build_aali_sft_v2.py): head+tail trim_to_budget (keeps the
  task + the failure/recovery ending, never splits tool-call JSON mid-object,
  always ends on an assistant turn); upweight copies exempt from dedup; a
  tool-message sanitizer (foreign tools + their orphaned results dropped,
  near-miss JSON repaired, nameless finals rewritten to {"tool":"final"},
  "{}" replies dropped) applied to mentor AND conversation-log records;
  write-time drops are named in the report, never silent; 13 new media-tool
  episodes (EN+AR) mirroring the exam's generate_image/generate_video
  behaviors with the real file_tools.py schema (the 2026-09-09 graduation
  failure was precisely that image-tool behaviors never took). Result:
  6,166 records, 35.5% Arabic, mentor data really in (19 clean + 13 failure
  ×3), 0 exam leaks, 0 unintended duplicates, every tool name inside Aali's
  25-tool registry, all arguments proper objects. Tests:
  tests/test_sft_v2_builder.py (10). Smoke gate verified live 2026-09-10:
  CPU dry-run of the 6-case probe against stale checkpoint-4326.
- **Agent honesty guards scoped to real tasks (2026-09-09)**: the
  success-claim and narrated-plan guards in agent_loop.py fired on pure chat
  ("قل لي فقط: جواب اختبار ٤٢" contains تم) and the brain parroted the guard
  lecture as its answer; now gated by _task_request() (AR/EN task-verb
  matcher, bias-True) so only real "do X" requests are policed. The few-shot
  protocol example moved INSIDE the system prompt — as chat messages the 7b
  brain echoed the example user text back ("ما هي أدواتك؟" → answered the
  example). Capability triggers extended (أدواتك / your tools…).
- **Admin audit log (2026-09-10)**: every admin action (key issue, key revoke,
  account delete) is appended to file_agent/audit_log.jsonl (gitignored,
  AALI_AUDIT_LOG redirect) — {ts, actor, actor_kind, action, target, detail,
  ip}, plaintext keys/passwords never enter a record, chat content stays out on
  purpose; trimmed to the newest 2000 entries. GET /api/admin/audit
  (admin-gated, ?limit/?action) + a «📜 سجل تدقيق الإجراءات» panel in the
  /admin dashboard (time, colored action tag, target, actor+kind, IP).
  file_agent/audit.py is stdlib-only, append-only, best-effort (a failed log
  write never breaks the action). Tests: tests/test_audit.py (6) +
  test_auth_http.py (3 HTTP: wiring+actor, admin gating, filter/limit).
- **Admin one-click handoff v2 (2026-09-10)**: the full session token no longer
  travels in a URL. The web app's admin pill (شارة «فريق») calls
  `POST /api/auth/handoff` (admin session only — anon/user/master-key all 401)
  and opens `/admin?ht=<single-use token>`; the dashboard immediately strips
  the query (history.replaceState) and trades the token server-side via
  `POST /api/admin/handoff-redeem` → `accounts.mint_session(email)` → a real
  admin session token saved to localStorage — the session token never appears
  in a URL or history. Handoff tokens live 60s, are consumed on first redeem
  (stored SHA-256-hashed like sessions), redeem is exempt from the global key
  gate (the ht token IS the credential) and grants only the minting account.
  Legacy `?token=` reader kept so older web builds still land. Both flows
  send the token in BOTH X-API-Key and X-Session-Token (the dashboard cannot
  tell which kind it holds — the server resolves X-Session-Token first; a
  session token alone in X-API-Key is rejected; master key works via
  X-API-Key). Audited: handoff_mint + handoff_redeem events (raw tokens never
  logged). Tests: test_auth_http.py (mint gating, single-use/forged/expired
  redeem, audit events) + test_accounts.py::test_mint_session_for_verified_account.
  README: admin section documents the flow + screenshots
  (docs/assets/auth_login.png, docs/assets/admin_dash.png).
- **/v1 provider surface + desktop 1.0.3 (2026-09-09)**: آلي يصبح موفّراً —
  file-agent/app.py يضيف `GET /v1/models` (يعرض aali, aali-local) و
  `POST /v1/chat/completions` (بالمخطط النمطي المتّبع في واجهات النماذج:
  non-stream + stream:true SSE وينتهي بـ data: [DONE])، بنفس المصادقة/سياسة
  الضيف للـ API العادي؛ فأي عميل يتكلم هذا المخطط (n8n، أدوات المحادثة
  المفتوحة…) يستخدم آلي كنموذج بـ Base URL
  `http://<host>:5055/v1`. docs/clients.md §3.5. Desktop overlay أضاف
  حبّات ربط المنصات تنسخ الإعدادات
  جاهزة؛ web: عدّاد الثواني يعدّ نبضات (+1/ثانية) بدل recompute فيتأخر
  عند خلفية التاب؛ نصوص «العقل» أوضح («إشعال عقل آلي…» → «آلي يفكر… N
  ثانية»). tests/test_openai_compat.py (5). Desktop v1.0.3: exe metadata
  1.0.3.0 (version_info.txt كان عالقاً على 1.0.0)، مثبّت
  build-desktop/installer/Aali-Desktop-Setup.exe، والأهم: cloudflared.exe
  مُدمج داخل الأنبوب (spec يلتقطه من build-desktop/) فزر «شارك آلي»
  يعمل من الصندوق — نسخة 1.0.2 المثبتة لم تكن تضمه.
  **بناء الـ exe**: الطريقة المعتمدة `scripts\build_desktop.bat`
  (venv مخصص `.venv-desktop` gitignored — ينشئه ويجمع exe + cli + installer؛
  حذفه يعني إعادة تنزيل كل شيء). بدائل يدوية عند الحاجة: PyInstaller ليس
  في .venv ولا global — استخدم
  `uv run --no-sync --with pyinstaller pyinstaller Aali-Desktop.spec
  --noconfirm --distpath build-desktop/dist --workpath build-desktop/work`
  (--no-sync ضروري وإلا حاول uv مزامنة torch وأفشل البناء)؛ فحص سريع:
  `uvx --from pyinstaller pyi-archive_viewer -l` يجب أن يظهر webview +
  cloudflared (بناء uvx خالص يفقد webview ويكسر النافذة؛ بدونه الحجم يقع
  من ~31MB إلى ~26MB — دليل تحذيري مفيد). ثم انسخ dist/*.exe إلى
  %LOCALAPPDATA%\Programs\AaliDesktop\ (نمط التحديث المعتمد).

— Last updated: 2026-09-12 (reboot kill + job breakaway: the 16:28:39 reboot
killed attempt 7 and the task-relaunched attempt 8 died in its first minute —
a scheduled task's job object sweeps a detached child that inherited it;
CREATE_BREAKAWAY_FROM_JOB now requested with a plain-flag fallback; status
digest calls a fresh-start-marker-over-silent-log STALLED, never RUNNING);
smoke-probe RAM gate: CPU probes pre-flight free RAM and skip fast (exit 4) when the GPU trainer starves the host — 2026-09-09-style three silent exit-3 cycles fixed; server output captured to smoke_server.log); 2026-09-10 (CLI bidi: Windows Terminal never trusted — CLI shapes Arabic itself on Windows (owner screenshot: mirrored greeting); admin one-click handoff v2: single-use 60s hashed ?ht= token traded server-side for a session — session token never in a URL; admin audit log: /api/admin/audit + dashboard «سجل تدقيق الإجراءات» panel — key issue/revoke, account delete with actor/time/IP; attachments LIVE: 📎 upload images/video/audio/docs with analyze-first OCR+Whisper; chat guards: no {} / echo / meta-leak / language naming + guest policy; /v1 provider surface + desktop auto-boot 1.0.3 (cloudflared bundled); ACCOUNTS: users | builders & team — email+password+code verify+reset, roles in one app (AALI_ADMIN_EMAILS), connection internals admin-only; brain auto-revive + dignified fallback; remote-brain provider for Pi hosting; git: ONE branch `main` (legacy snapshots merged, old branches archived as tags); branding: Aali is the only AI — built & trained by team HWK, external provider names scrubbed from user-facing text; sharing hardening: fail-safe bind + aali_share.bat + gated tunnel; CLI bidi v2 wrap + extended letters; calm-glow web v2; Phase A done, Phase B 4096 relaunched after accidental close; earlier: owner brain tree + event feed + vault; aali_deploy publishing menu; new theme/icon; soup graduation queued; Aali-as-a-product server + streaming + multi-user + desktop app, memory + security upgrade, OmniRoute + Soup, edit_image/edit_video + machine_ops, mentor learning loop; sft-now remains condemned — re-SFT with the rebalanced mix + mentor episodes at ≤3 epochs, promote only on the exam)
