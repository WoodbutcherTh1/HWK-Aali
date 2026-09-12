# HWK Aali — Full Project Handoff (snapshot 2026-09-10)

> Purpose: a complete, self-contained briefing for any AI or engineer taking
> over or auditing this repository. Facts verified against the live work tree
> on 2026-09-10. The canonical, always-current contract is `AGENTS.md` at the
> repo root — read it too; where this file and AGENTS.md disagree, AGENTS.md wins.

---

## 1. What this project is

**Aali (آلي)** — a home-built AI assistant system by owner **HmamK** (team HWK)
on a single Windows PC (8GB-VRAM GPU, 16GB RAM, ~2TB across C:/D:/X:), with a
MacBook for light work. Three pillars, all in one repo:

1. **A model built from scratch** — a ~125M-param decoder-only transformer
   (RoPE positional embeddings, LayerNorm, GELU MLP) trained on the owner's own
   data. Phase A pretraining is COMPLETE (exactly 90,000 steps / 2.95B tokens,
   verified in `D:/hwk-models/scratch/trainer-state.pt`); Phase B is a context
   extension 1024→4096, actively training (live checkpoint passed step 59,000 /
   1.93B tokens during this snapshot's audit; it had once been accidentally
   closed at step 42,475 and relaunched — resumable).
2. **A real agent** — file/command agent (sandboxed to the workspace) + OCR,
   document, video, image tools, so the model grows into an assistant, not a toy.
3. **Many clients, one API** — Web (Arabic-first RTL), Windows desktop shell,
   terminal CLI (bidi-correct Arabic), iOS app (SwiftUI, built on the MacBook) —
   all talking to one Flask API on port **5055**.

**Branding rule:** Aali is the *only* AI. External provider names are scrubbed
from user-facing text. External desktop/web assistants are used only as
*mentors* to generate training data.

**Non-negotiable rules** (full list in AGENTS.md §3):
- Tests must stay green: `scripts\run_tests.bat` (pytest, currently 219 passing).
- Never kill python/node processes by guess — look up the exact PID owning the
  port first (`netstat -ano | grep LISTENING`). This killed a 12h job once.
- Never `git push` unless the owner says so; one branch `main` (old branches
  archived as tags).
- Data lives OUTSIDE the repo (D:/hwk-data/, X:/hwk-backups/) — never commit
  datasets, logs, keys, or account stores.
- Never break the sandbox: agent writes stay in `file-agent/agent_workspace/`;
  the command allow-list must not be widened casually.
- Arabic-first RTL UI text everywhere.

---

## 2. Repository map (every directory, verified 2026-09-10)

| Path | What it is |
|---|---|
| `AGENTS.md` | THE contract for any agent/human. Read fully before touching anything. |
| `README.md` | User-facing docs (Arabic): install, clients, admin dashboard, screenshots. |
| `file-agent/` | **The brain.** Flask server `app.py` (~1.8k lines) + agent loop + tools + model code. |
| `file-agent/file_agent/` | Tool/feature modules (see §4). |
| `file-agent/hwk_model/` | The from-scratch model: `model.py` (RoPE transformer), `generation.py`, `bpe_tokenizer.py`, `tokenizer.py`. |
| `file-agent/agent_workspace/` | The agent's sandbox — every file write lands here; served via `/api/file/<path>`. Gitignored. |
| `file-agent/*.jsonl` | Runtime stores: `accounts.jsonl`, `api_keys.jsonl`, `sessions.jsonl` (gitignored). `audit_log.jsonl` created on first admin action. |
| `web/` | Arabic-first web client. `src/` = TypeScript/React source (vite); `dist/` = built bundle served at `/ui/` by the server. PWA icons, signup page, service worker. |
| `scripts/` | ~60 one-click `.bat` runbooks + tool CLIs + automation daemons (see §5). |
| `tests/` | pytest suite — **14 files, 219 tests, all green**. Must stay green. |
| `docs/` | 12 feature/deployment docs (see §7). `assets/` has screenshots + aali_tour.gif. |
| `rules/` | `assistant_rules.md` — Aali's behavior rules (taught via SFT). |
| `skills/` | 12 skill docs the agent loads (security, PC commands, emoji etiquette, n8n, Priority ERP, media, web research…). |
| `data/` | SFT datasets: `sft_mix.jsonl` (main), `teacher_sft.jsonl` (mentor captures), tool-calling sets, exam sets, AR/EN question banks. |
| `ios/Aali.xcodeproj` | Native SwiftUI iOS client — built on the MacBook, not on this PC. |
| `colab/`, `mentor_data/` | Google Colab quickstart for external GPU bursts (`HWK_colab_quickstart.py`). |
| `legacy/` | Old training/eval scripts kept for reference. |
| `deploy/` | `hf_space/` (Hugging Face Space) + `oracle/` deployment assets. |
| `build-desktop/` | PyInstaller workdir + installer config (`Aali-Setup.iss`, specs, icons, `version_info.txt`). `dist/` + `installer/` are gitignored build outputs. |
| `.github/workflows/tests.yml` | CI running the pytest suite. |
| `.replit`, `.freebuff/` | Hosting-tool config + Freebuff agent-workspace runtime (gitignored logs/scratch). Harmless to the project itself. |
| Root scripts | `train_scratch.py` (Phase A/B trainer, resumable, mirrors to X:), `tokenize_corpus.py`, `download_corpora.py`, `download_github_corpora.py`, `train_bpe.py`, `teacher_to_sft.py`, `generate_tool_sft.py`, `mentor_a.py`–`mentor_d.py` (mentor capture automation), `evaluate_scratch.py`, `probe_model.py`, `hwk_paths.py` (central path helper — never hardcode user paths), `desktop_app.py` (older shell), `aali_desktop_app.py` (current desktop app), `aali_cli.py` (root shim → scripts/), `Aali-Desktop.spec`, `soup.yaml`, `docker-compose.yml`, `Dockerfile`, `check_env.py`, `create_*_data.py`, `prepare_data.py`, `win_ocr.ps1`, `export_project.sh`. |
| `.venv/`, `.venv-desktop/` | Python venvs (desktop build venv is gitignored; deleting it means re-downloading everything). |
| `_scratch/` | Gitignored scratch area for one-off agent work. |

---

## 3. The server — `file-agent/app.py` (the single brain)

Flask app, port **5055** (`PORT` env; standard ports: 5055 brain, 5678 n8n).
Bind is fail-safe: no `AALI_API_KEY` → binds **127.0.0.1 only**; key mode binds
0.0.0.0 (LAN sharing via `scripts/aali_share.bat`); `AALI_BIND` overrides.
Dockerfile + compose exist for server deployment.

**Core API:**
- `POST /api/ask {message, sid, policy, confirm, attachments}` → `{ok, reply, sid, needs_confirm?, pending_action?, suggestions?}` — the agent loop endpoint.
- `POST /api/ask/stream` — SSE: live tool-activity events (`agent_log` bus) + final `done` frame.
- `GET /api/health` — health (also used by clients to auto-boot the server).
- Sessions stored server-side `sessions.jsonl`, per-user namespaced (`u<key_id>:<sid>`), TTL 30 days, MAX_TURNS 100.

**Accounts & keys (one app, two roles):**
- `file_agent/accounts.py`: email+password signup → 6-digit code verify (no
  SMTP yet — codes surface in server log / `dev_code` when `AALI_AUTH_DEBUG=1`;
  note it defaults ON, so set `AALI_AUTH_DEBUG=0` on any shared deployment)
  → PBKDF2-SHA256 password hashes, SHA-256 session tokens (30d). Admin emails
  via `AALI_ADMIN_EMAILS` (default `hmam@hwk.team`) get role=admin at login.
  `mint_session(email)` — internal-only session minting for the handoff flow.
- `file_agent/apikeys.py`: the provider key platform — `aali-<hex>` keys,
  SHA-256-hashed at rest, per-key usage metering (requests/chars), optional
  daily cap (`AALI_KEY_DAILY_CAP`), self-serve signup rate-limited per IP
  (`AALI_OPEN_SIGNUP=0` disables). Master key never stored.
- Auth resolution order: master `AALI_API_KEY` (admin) → account session
  (`X-Session-Token`) → issued API key (`X-API-Key`). A session token alone in
  X-API-Key is rejected. `_require_admin()` = master key OR admin-role session;
  single-user local mode (no key) keeps admin open on localhost.
- Guest policy: remote non-admin keys get `policy=guest` enforced SERVER-side
  (loopback + no X-Forwarded-For = local; spoof-proof) — run_command /
  machine_ops / delete_file / move_file / memory hard-blocked. `confirm` is
  client-supplied and NOT a security boundary.
- Attachments: `POST /api/attach` (≤4 files, 100MB cap) → saved into the
  sandboxed `uploads/`, analyzed IMMEDIATELY (Windows OCR for images, doc
  parser for pdf/docx/xlsx, Whisper + frame-OCR for video/audio) so the ask
  never waits; client sends `attachments:[names]`, server re-validates and
  injects an Arabic context block — client analysis never trusted.

**Admin surface** (all under `/api/admin/*`, admin-gated):
- Keys: list / issue (plaintext shown ONCE) / revoke; account listing / delete.
- `GET /api/admin/audit` — the audit trail (see §4).
- `GET /api/admin/stats` — dashboard data. `/admin` — self-contained dashboard
  page (Arabic RTL): stats cards, issue-key form, keys table with revoke,
  📜 audit-log panel, 🧠 brain-tree live panel. Opens via one-click handoff
  (below) or master key. `?demo=1` previews in single-user mode.
- One-click handoff **v2**: the web app's «فريق» badge calls
  `POST /api/auth/handoff` (admin SESSION only; anon/user/master all 401) and
  opens `/admin?ht=<single-use token>` (60s TTL, SHA-256-hashed at rest,
  consumed before validation). Dashboard strips the URL immediately and redeems
  via `POST /api/admin/handoff-redeem` → real session token into localStorage.
  **The session token never appears in a URL.** Legacy `?token=` reader kept
  for older web builds. Audited as `handoff_mint`/`handoff_redeem`.
- Brain tree: `/brain` (owner-only live map of every flow, each step citing its
  real code file), `/api/brain/live` JSON, SSE event feed, Obsidian vault export
  (`scripts/build_brain_vault.py`), short-lived `bt:` visit tokens so the
  master key is never in a URL.

**Provider surface:** `GET /v1/models`, `POST /v1/chat/completions`
(OpenAI-style schema, non-stream + SSE `data: [DONE]`), same auth/guest policy
— any OpenAI-schema client (n8n etc.) can use Aali as a model with
Base URL `http://<host>:5055/v1`.

**Model routing:** `providers.py` — remote-brain provider for Pi hosting;
brain auto-revive + dignified fallback. Active brain is selectable (own model
⇄ local qwen2.5:7b-instruct via Ollama, num_ctx 8192 `AALI_OLLAMA_NUM_CTX`).

---

## 4. `file-agent/file_agent/` — feature modules

| Module | What it does |
|---|---|
| `file_tools.py` | The agent's tools; registry in `_FUNCTIONS` + `_DEFINITIONS`; sandbox via `_resolve()`; command allow-list. New tools MUST be registered here + tested. Includes `generate_emoji` (Pillow, CPU), `edit_image`, `edit_video`, `machine_ops` (PC control, admin-gated), `delete_file`/`move_file`. |
| `memory.py` | Long-term memory (LIVE): save/recall/forget/summary; store `%USERPROFILE%\.aali\` (redirect `AALI_MEMORY_DIR`); newest-instruction-wins per topic, contradictions surfaced ("earlier you told me X"), secrets auto-redacted, forget is confirmation-gated. |
| | Semantic recall: hybrid keyword + embedding cosine (Ollama nomic-embed-text, vectors cached in ~/.aali/); honest `mode` field; keyword fallback. |
| `accounts.py` | Users/builders account platform (see §3). |
| `apikeys.py` | API-key platform (see §3). |
| `audit.py` | **NEW 2026-09-10**: append-only JSONL audit log (`audit_log.jsonl`, gitignored, `AALI_AUDIT_LOG` redirect; trimmed to 2000; best-effort — never breaks the action). Records `{ts, actor, actor_kind, action, target, detail, ip}` for `key_issue`, `key_revoke`, `account_delete`, `handoff_mint`, `handoff_redeem`. Plaintext tokens/passwords never logged; chat content excluded by design. |
| `brain_tree.py` | The /brain live tree data (flows citing real code files). |
| `suggestions.py` | Up-to-3 safe language-matched follow-up chips on every reply. |
| `emoji.py` | 8-emotion model on user emojis (AR+EN); decorates replies with ≤1 fitting emoji — never on errors/security/serious topics. |
| `autocorrect.py` | Chat-speak/typo correction ("hai dode"→"hi dude") + Arabic normalization; preserves paths/code/quotes/numbers; polite corrected-reading hint. |
| `doc_tools.py` | pdf/docx/xlsx parsing for attachments. |
| `skills.py` | Loads the `skills/` docs into the agent. |
| `n8n_gen.py` | n8n workflow generation helper. |
| `web_tools.py` | Web research tooling. |

**Agent loop (`agent_loop.py`) honesty guards (all LIVE):**
- Raw shell-JSON can never reach the user (parse → retry ×2 with feedback →
  honest apology).
- Echo guard rejects verbatim parrots of the user message.
- Language guard NAMES the target language (الإنجليزية فقط / العربية فقط).
- Success-claim/narrated-plan guards gated by `_task_request()` so pure chat
  isn't policed; few-shot protocol example lives INSIDE the system prompt.

---

## 5. Clients

**Web (`web/`)** — React+TS+vite, Arabic-first RTL, warm-black + gold theme,
dark/sepia switch, streaming chat with live tool activity, markdown + tables +
language-colored code fences, sessions sidebar, voice input, attachments (📎
with image previews), suggestion chips, time-aware Arabic greeting, shareable
`?sid=` deep links, PWA (manifest + service worker + icons), GitHub-token
client-side integration. Built bundle committed at `web/dist` (served at
`/ui/`); rebuild = `npm run build` in `web/`. **Admin pill** opens the
dashboard via the single-use handoff (§3).

**CLI (`scripts/aali_cli.py`)** — pro terminal REPL: SSE tool traces, spinner,
`/new /open /clear /theme gold|matrix|ocean /tools /multi /sid /help /exit`,
y/N danger confirms, raw-mode LineEditor (history ↑/↓, editing keys, TAB
completion, paste-safe multi-line). **Arabic bidi pipeline**: `fx()` =
width-aware wrap (logical order) → `shape_arabic` (contextual presentation
forms, lam-alef ligatures, harakat pass-through, extended Arabic-script
letters from Unicode decomposition) → `reorder_visual` (RTL runs reversed,
marks attached, brackets mirrored, LTR runs kept). Ships as
`build-desktop/dist/aali-cli.exe` with a Start-Menu entry. Ships stdlib-only.
**Bidi auto-detect (fixed 2026-09-10):** platform-aware — on Windows the CLI
ALWAYS shapes itself (Windows Terminal/conhost/xterm.js render Arabic
disconnected+mirrored; `WT_SESSION` is no longer trusted); ConEmu and
mintty/WezTerm/iTerm get an explicit pass; macOS/Linux terminals are trusted
(CoreText/HarfBuzz). `/bidi on|off|auto` (+ `--bidi`/`AALI_BIDI`) persisted in
`~/.aali_cli_bidi` is the manual escape hatch. Auto-starts the server via
`start_app.bat` when absent.

**Desktop (`aali_desktop_app.py`)** — pywebview shell, v1.0.3: auto-boots the
server (`_ensure_local_server`, hidden `start_all.bat`, repo discovery via
`AALI_HOME` → `%APPDATA%\AaliDesktop\repo.txt` → search paths), streaming UI,
share button (bundled `cloudflared.exe`), ships as `Aali-Desktop.exe` +
installer `Aali-Desktop-Setup.exe`. Installed exes update in place to
`%LOCALAPPDATA%\Programs\AaliDesktop\`. Build via `scripts\build_desktop.bat`
(custom `.venv-desktop`, PyInstaller `--no-sync` pattern — see AGENTS.md for
the exact manual commands and pitfalls).

**iOS (`ios/Aali.xcodeproj`)** — SwiftUI client, built on the MacBook.

---

## 6. Data, training, and the pipeline

**From-scratch model (`hwk_model/` + root trainers):**
- Tokenizer: BPE 32k. Corpus: pile 18.8B+ tokens (~2358 shards) + Arabic corpus
  (`fetch_arabic_corpus.py`: 4,000 Wikisource dump pages + 105K hotel-review
  lines verified) → `tokenize_corpus.py` → token shards on D:/X:.
- `train_scratch.py`: Phase A/B trainer — resumable, mirrors checkpoints to
  `X:\hwk-backups\`. Phase A COMPLETE (90k steps / 2.95B tokens, ctx 1024,
  d_model 768). Phase B = context extension to 4096 via `extend_context.bat`
  (relaunched after an accidental close at step 42,475).
- GPU gate: `scripts/wait_gpu_free.py` is THE shared gate (library + fail-closed
  CLI, exit 2 = NO GO): python/soup/ptxas compute processes + VRAM <1500 MiB +
  training-log-idle checks. Every launcher delegates here — no per-script
  nvidia-smi polling copies left.

**Soup (the local teacher / exam model)** — `soup.yaml` +
`D:/hwk-tools/soup-venv/` (fine-tune/serve CLI):
- `scripts/soup_pipeline.py`: waits for a free GPU (via the gate) → serves the
  Soup teacher → runs the 24-case baseline exam → re-SFTs on `sft_v2.jsonl`
  → grades the tuned model → writes the promotion verdict. Smoke gate
  (`soup_probe_smoke.py`): watched training — probes the newest checkpoint
  every ~6.5 min on CPU (port 20130) and ABORTS at 2 consecutive failed probes;
  the tuned adapter is served + health-checked before grading (the phantom
  0/26-verdict bug is fixed); on PROMOTE that server STAYS UP as the live brain.
- Night caretaker (`scripts/night_caretaker.py`): watches Phase B
  (context_training.log) every 10 min until the GPU frees, then audits+prunes
  lab episodes (`audit_mentor_lab.py` — keeps only verified-working
  episodes), rebuilds sft_v2 + soup export, launches the soup pipeline, waits
  for its verdict, writes `D:/hwk-data/MORNING_REPORT.md`. It no longer
  resumes Phase A (done) or chains the 4096 extension (that IS Phase B — a
  second copy would collide with the pipeline on the 8GB card). Relaunch it
  detached if a Freebuff restart kills it.
- `sft_v2.jsonl` = 6,166 records at the 2026-09-10 audit (rebuilt after the
  fixes below), 35.5% Arabic (rebalanced from 1.4%): capped mix + tools +
  mentor data actually present (19 clean + 13 failure episodes ×3) + live
  chats + memory/security episodes + 13 media-tool episodes; dedup +
  exam-leak gated, all tool names validated against Aali's registry. Audit
  corrections (build_aali_sft_v2.py): every mentor episode had been silently
  dropped at write time for exceeding MAX_TOTAL_CHARS=2200 — now
  head+tail-trimmed to budget (tool JSON never split, ends on an assistant
  turn); the ×3 failure upweight copies are exempt from dedup (byte-identical
  copies were being killed before writing); a tool-message sanitizer removes
  the external mentors' leaked toolset (Read/Edit/Bash/Grep/Write with
  stringified args), repairs near-miss tool JSON, rewrites nameless
  {"content": ...} replies to the {"tool":"final"} protocol shape and drops
  "{}" replies — in mentor AND conversation-log records; write-time drops
  are named in the report, never silent. Tests: tests/test_sft_v2_builder.py.
  **sft_now remains CONDEMNED** — re-SFT with the rebalanced mix at ≤3
  epochs, promote only on the exam.
- Teacher data: 87+ SFT records from external mentors (`mentor_*.py`,
  `scripts/mentor_capture.py`); mentor lab automation via Colab bundle.

---

## 7. Docs index (`docs/`)

`ai_security_lessons.md` (industry-incident lessons enforced in code),
`clients.md` (all clients + /v1 provider usage), `custom_domain.md`
(aali.dpdns.org named tunnel), `desktop_app.md`, `image_generation.md`,
`long_term_memory.md`, `mentor_learning_loop.md`, `post_phaseA_pipeline.md`,
`publish_aali.md` (HF/Ollama/GGUF deploy menu), `server_deployment.md`,
`soup_setup.md`, `training.md`. Plus `docs/assets/` screenshots and
`scripts/make_aali_guide.py` which renders Aali-Guide-<date>.pdf to the Desktop.

**Security posture summary:** lessons from real 2023–2025 incidents + OWASP
injection taught in all four system prompts (EN+AR) and `skills/security-rules.md`;
run_command blocks env-dumping and redacts credentials; Aali never exposes his
own prompts/code/env/logs; fail-safe bind; server-enforced guest policy;
key/session/handoff tokens all hashed at rest; master key never in URLs;
`aali_tunnel.bat` refuses to expose an unauthenticated server;
`aali_share.bat` (owner-only) generates the master key + LAN-only firewall rule.

---

## 8. Tests & verification

- 14 test files / **219 tests, all passing** (2026-09-10): model sanity
  (`test_hwk`), CLI + CLI bidi (38 combined), accounts/auth HTTP (incl. handoff
  v2: mint gating, single-use/expired/forged redeem, dual-header combos),
  audit log (unit + HTTP), attachments, agent honesty guards, brain tree,
  OpenAI-compat `/v1`, remote brain, server sharing/guest policy, soup smoke
  gate, GPU gate.
- Run: `scripts\run_tests.bat` (or `PYTHONPATH=file-agent .venv/Scripts/python.exe -m pytest tests -q`).
- CI: `.github/workflows/tests.yml`.

---

## 9. Runtime state & environment (as of 2026-09-10)

- **Live brain**: server running on 127.0.0.1:5055 (PID 16660 at snapshot
  time). Active brain in CLI banner: `qwen2.5:7b-instruct` (Ollama fallback
  while Phase B trains). n8n on 5678 needs one manual activation click; ffmpeg
  installed but PATH needs refresh in new shells.
- **Long-running jobs** (owner-side, this PC): Phase B context extension
  (GPU), soup pipeline queued, night caretaker on duty. Logs to
  `D:/hwk-data/*.log`. Never run training on <20% free disk; never delete
  checkpoints on X:.
- **Git**: single branch `main`, HEAD = the bidi-detection fix commit
  ("cli bidi: Windows Terminal never trusted — CLI shapes Arabic itself"),
  which contains `scripts/aali_cli.py` (platform-aware
  `_terminal_bidi_capable()`: Windows terminals never trusted, the CLI shapes
  Arabic itself there), `tests/test_cli_bidi.py` (3 new tests), and the
  `AGENTS.md` bidi v2-fix entry — plus the audit-corrections commit that
  committed this handoff document itself. Working tree clean at snapshot.
  Nothing pushed (rule: only when the owner says so). Audit note: the
  architecture is RoPE + LayerNorm + GELU MLP (earlier SwiGLU/RMSNorm claims
  in AGENTS.md/brain_tree.py were wrong and were corrected in the audit).
- Python 3.12; deps pinned in `requirements.txt` (root: torch/transformers/
  datasets/peft/bitsandbytes/diffusers/opencv… training+media stack) and
  `file-agent/requirements.txt` (server: requests, Flask, Pillow). No new heavy
  deps in the training venv; multimedia goes to `D:/hwk-tools/sd-venv` or
  subprocesses.

---

## 10. What to check first as a newcomer

1. `AGENTS.md` — the contract (§5 Status is the living changelog).
2. `file-agent/app.py` — one file holds the whole HTTP surface; search by
   `@app.route`.
3. `file-agent/agent_loop.py` — the agent brain + honesty guards.
4. `tests/` — the fastest way to learn intended behavior; keep 219 green.
5. `docs/` for any feature you touch — each live feature above has a doc.
6. Verify claims against the live server: `curl http://127.0.0.1:5055/api/health`.
