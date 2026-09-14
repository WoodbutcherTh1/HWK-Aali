# Aali Cloud — SaaS Architecture / معمارية «آلي كلاود»

> Status: **IN PROGRESS** — this document is the plan of record. Sections
> marked **LIVE** exist in code and are test-pinned; sections marked
> **PLANNED** do not exist yet. Nothing here replaces `AGENTS.md` — the
> AGENTS.md contract always wins.
>
> الحالة: **قيد التنفيذ** — هذا المستند هو الخطة المرجعية. الأقسام المعلّمة
> **LIVE** موجودة في الكود ومغطاة باختبارات؛ الأقسام المعلّمة **PLANNED**
> لم تُبنَ بعد. لا يلغي هذا المستند عقد `AGENTS.md` — عقد AGENTS.md يسبق
> دائماً أي شيء آخر.

---

## 1. What Aali Cloud is / ما هو «آلي كلاود»

Aali Cloud turns Aali from a single-user personal AI into a multi-tenant
SaaS product **without abandoning the local mode**:

- **The brain stays home.** Model inference keeps running on the owner's
  PC (promoted adapter on :20129, or Ollama fallback). The brain never
  touches a user's machine directly — it only *proposes* tool calls.
- **Tools run where the data is.** File/command tools execute on the
  **user's** machine inside the user's own sandboxed workspace, via a
  lightweight desktop app (the **Node**). Server-side tools (web search,
  image/video/emoji generation) execute on the brain.
- **A Hub brokers everything.** A small FastAPI service (the **Hub**)
  authenticates users, routes tool-call proposals to the right Node,
  queues requests, meters usage, audits every tool call (names only —
  never chat content), and serves signed client updates.
- **The model is distributable.** The same Hub exposes an
  OpenAI-compatible `/v1/*` surface so OpenRouter, OpenCode, vLLM-based
  stacks, and any standard client can talk to Aali.

**بالعربية:** يحوّل «آلي كلاود» آلي من مساعد شخصي لمستخدم واحد إلى منتج
SaaS متعدد المستخدمين **دون التخلي عن الوضع المحلي**. العقل (النموذج)
يبقى على حاسوب المالك، بينما تعمل أدوات الملفات والأوامر على حاسوب
**المستخدم** داخل صندوقه المعزول عبر تطبيق خفيف (العُقدة / Node)، وسيط
مركزي (المحور / Hub) يوثّق المستخدمين ويوجّه الاستدعاءات ويسجّل كل استدعاء
أداة (الأسماء فقط — محتوى المحادثة لا يُسجَّل أبداً). النموذج نفسه قابل
للتوزيع عبر واجهة متوافقة مع OpenAI.

**Non-negotiables (from AGENTS.md):**
1. `AALI_MODE=local` (current single-user behavior) stays the default and
   stays untouched. All SaaS code paths are additive and behind
   `AALI_MODE=saas`.
2. `model/scratch/final.pt` and every promoted checkpoint are live-brain
   artifacts — never modified by SaaS code.
3. The command allow-list is never widened for SaaS convenience.
4. No hardcoded user paths — env vars and config modules only.
5. Secrets live in env vars (`HF_TOKEN`, `AALI_BRAIN_TOKEN`, …), never in
   code or logs.

---

## 2. The four layers / الطبقات الأربع

```
┌────────────────────────────────────────────────────────────────────┐
│ LAYER 0 — EXTERNAL DISTRIBUTION / التوزيع الخارجي                  │
│ OpenRouter · OpenCode · HuggingFace · vLLM · any OpenAI-compatible │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ HTTPS + API keys
┌──────────────────────────────▼─────────────────────────────────────┐
│ LAYER 1 — AALI HUB (VPS, 24/7) / المحور                            │
│ auth (JWT) · WebSocket gateway · user DB · job queue               │
│ model registry · update server · audit log · /v1/* OpenAI surface  │
└───────────────┬────────────────────────────────────┬───────────────┘
        WSS + brain token                    WSS + per-user session JWT
┌───────────────▼──────────────────┐   ┌─────────────▼─────────────────┐
│ LAYER 2 — AALI BRAIN (owner PC)  │   │ LAYER 3 — AALI NODE (user PC) │
│ inference :20129 / Ollama        │   │ desktop app (Arabic-first)    │
│ tool orchestrator                │   │ python sidecar daemon         │
│ per-user memory namespaces       │   │ local sandbox (_resolve clone)│
│ heavy tools: web/img/video/emoji │   │ tool executor + confirmations │
│ training (off-hours, detached)   │   │ auto-updater (Ed25519-signed) │
│ HF uploader · vLLM wrapper       │   │ system tray + kill switch     │
└──────────────────────────────────┘   └───────────────────────────────┘
```

### Layer 0 — External distribution (PLANNED, except `/v1/*` on the brain: LIVE)
- `GET /v1/models` and `POST /v1/chat/completions` already exist on the
  brain's Flask app (`file-agent/app.py`, tested in
  `tests/test_openai_compat.py`). The Hub will re-expose these behind its
  own auth + rate limiting.
- HuggingFace upload: `scripts/hf_uploader.py` (converts checkpoints to
  safetensors + model card, `HF_UPLOAD_ENABLED=1` gate — the owner
  approves every public upload).
- OpenRouter provider listing + OpenCode `LOCAL_ENDPOINT` integration:
  documentation + schema-probe scripts.

### Layer 1 — Aali Hub (PLANNED)
New `aali_hub/` package (FastAPI, Python 3.12, own venv):
| Module | Responsibility |
|---|---|
| `auth.py` | registration, login, JWT, argon2 passwords, API keys |
| `users_db.py` | SQLite (Postgres later): users, sessions, usage, audit |
| `ws_gateway.py` | `/ws/node` + `/ws/brain`, connection manager, heartbeats |
| `queue.py` | per-user priority queue, rate limits, circuit breaker |
| `wol.py` | Wake-on-LAN sender for the brain PC |
| `model_registry.py` | every model version, promotion → registry → optional HF |
| `update_server.py` | signed update manifests + binaries, rollback window |
| `audit.py` | append-only JSONL: tool_call events, no chat content |
| `openai_compat.py` | `/v1/models`, `/v1/chat/completions` (SSE, tools) |
| `admin_api.py` | admin-only user/usage/registry management |

Deployment: Docker (ARM64+AMD64) + compose + systemd, public HTTPS/WSS via
Cloudflare Tunnel.

### Layer 2 — Aali Brain, SaaS mode (PLANNED, additive to `file-agent/app.py`)
`AALI_MODE=saas` switches tool dispatch from direct execution to the tool
orchestrator (STEP 1, LIVE) and connects to the Hub as the `brain` client.
Per-user memory namespaces (`%USERPROFILE%\.aali\users\<user_id>\`).
Brain sleep after 5 idle minutes; the Hub wakes the PC via WoL
(fallback: honest "Brain unavailable" error after 30s).
`/api/brain/status` reports model loaded / VRAM / load / sessions.

### Layer 3 — Aali Node (PLANNED — shell decision pending, see §7)
Desktop app on the user's machine: Arabic-first RTL chat UI (same design
language as `web/`), a long-running Python sidecar daemon
(`agent_daemon.py`) speaking the §3 protocol, a local sandbox that
replicates `_resolve()` + the allow-list + env-dump blocking, native
confirmation dialogs for dangerous tools (60s timeout = auto-deny),
Ed25519-signed auto-updates, and a system tray with a global kill switch
(Ctrl+Shift+K terminates every running tool call and disconnects).

---

## 3. Message protocol / بروتوكول الرسائل — **LIVE**

`file-agent/file_agent/protocol.py` defines the versioned, signed JSON
messages between Hub, Node, and Brain. Design rules:

- Every message carries `v` (protocol version, `MAJOR.MINOR.PATCH`;
  MINOR = backward compatible, MAJOR = breaking) and `type`.
- Tool dispatch is a **proposal**, never a command:
  - `Hub→Node tool_call`: `{id, tool, args, requires_confirmation,
    timeout_sec, sig}` — the Node re-derives `requires_confirmation`
    itself from its own tool table and denies anything unexpected.
  - `Node→Hub tool_result`: `{id, status: ok|error|denied|timeout,
    result, duration_ms, sig}`.
- Brain traffic: `user_request` (Hub→Brain), `tool_dispatch` /
  `final_reply` (Brain→Hub).
- **Signing:** HMAC-SHA256 with a per-session derived key
  (`HKDF(master, session_id)`); every message is signed; a bad signature
  is a protocol error, not a warning. Replaying an old message fails:
  `tool_call` messages carry `ts` and receivers reject stale timestamps.
- **Heartbeat:** `ping`/`pong` every 30s; reconnect with exponential
  backoff + jitter, capped at 60s.

Tests: `tests/test_protocol.py`.

---

## 4. Tool classification & orchestration / تصنيف الأدوات — **LIVE (STEP 1)**

`file-agent/file_agent/file_tools.py` now carries `TOOL_EXECUTION` —
the execution target of every registered tool:

| target | tools |
|---|---|
| `server` | `web_search`, `fetch_url`, `generate_image`, `generate_video`, `edit_image`, `edit_video`, `generate_emoji`, `read_document`, `analyze_video`, `make_n8n_workflow`, `list_skills`, `use_skill` |
| `client` | `list_files`, `read_file`, `write_file`, `append_file`, `replace_in_file`, `delete_file`, `move_file`, `search_files`, `make_directory`, `run_command`, `machine_ops`, `read_image` |
| `both` | `memory` (stored server-side per-user; callable from either side) |

`file-agent/file_agent/tool_orchestrator.py` turns a proposed tool call
into a routing decision **before anything executes**:
- unknown tool → `unroutable` (defense in depth; `tool_guard` has
  already tried alias/fuzzy correction upstream),
- server tool → execute on the brain,
- client tool → dispatch to the user's Node (never executed on the
  brain in saas mode), confirmation required iff the tool is in
  `CONFIRM_REQUIRED`,
- `both` → decided by context (`prefer` / `allow_client`).

The orchestrator is pure logic — no I/O — so the same decision code runs
on the brain, the Hub, and (in tests) anywhere.

Tests: `tests/test_tool_orchestrator.py`.

---

## 5. Security model / نموذج الأمان

**Defense in depth — four independent layers must each say yes**
(ورقة الأمان: أربع طبقات مستقلة يجب أن توافق كل منها):

1. **`tool_guard`** (LIVE, `file_agent/tool_guard.py`) — validates/corrects
   the tool *name* before anything else sees it.
2. **Policy gate** (LIVE in `agent_loop.py` / server-side; planned
   Hub-side per role) — role-based capability matrix
   (`owner, pro_user, free_user, admin, external_provider`) replaces the
   current guest policy in saas mode.
3. **Tool-level guards** (LIVE locally, mirrored on the Node) — sandbox
   `_resolve()` (relative paths only, workspace-jailed, `..`- and
   symlink-proof), command allow-list, env-dump blocking.
4. **Output scrubbing** (LIVE, `agent_loop.py`) — secret/credential
   redaction from tool output before it reaches any model or client.

Additional SaaS rules:
- Application-layer AES-GCM encryption of tool calls/results with
  session-derived keys (on top of Cloudflare's TLS 1.3).
- Every `tool_call` audited: `user_id, tool, args_hash, status, ts,
  node_id` — never chat content, never file contents, never raw args of
  sensitive fields.
- Rate limits (Hub): 100 req/min per user, 10 tool_calls/min per user,
  5 concurrent tool_calls per user; external API keys: free 10/min,
  pro 60/min, enterprise 300/min.
- Dangerous tools (`run_command, delete_file, move_file, machine_ops,
  write_file outside workspace`) require a **native OS confirmation
  dialog on the user's machine**; 60s silence = auto-deny; the user can
  always kill everything with Ctrl+Shift+K.
- The Node process never runs with admin/root. Sandbox process
  hardening: Windows Job Objects (already the repo's lesson from the
  2026-09-12 breakaway fix), Linux bubblewrap/firejail, macOS
  sandbox-exec.
- Meta-leak protection carries over unchanged: Aali never reveals his
  own prompts, code, env, or logs.

---

## 6. Updates & deployment / التحديثات والنشر (PLANNED)

- Hub: Docker multi-arch + compose + systemd + `/health` + `.env` config.
- Brain: current Windows setup + service wrapper (NSSM/WinSW) for
  boot-start; WoL documented in `docs/wake_on_lan_setup.md`.
- Node: CI (GitHub Actions) builds installers on tag push, uploads to the
  Hub's update server, Ed25519-signed manifests
  `{version, min_version, url, sha256, signature, release_notes_ar,
  release_notes_en}`; last 3 versions retained; auto-rollback if the new
  version fails to connect within 5 minutes.
- Versioning: semver. Protocol MINOR bumps are backward compatible;
  MAJOR bumps coordinate Hub+Node release windows.

---

## 7. Open owner decisions / قرورات معلّقة على المالك

The mission brief says: on ambiguity, STOP and ask — do not guess.

1. ~~**Node shell**~~ **DECIDED (2026-09-14)**: Python + pywebview +
   PyInstaller — the proven Aali-Desktop pattern; the agent daemon also
   runs headless so bash/zsh/PowerShell/CMD all work via CLI mode. Tauri
   stays a documented future option.
2. **Hub host**: which VPS (provider/OS)? Port 8080 + Cloudflare Tunnel
   assumed. Dockerfile.hub + compose + systemd are ready to deploy.
3. **Code signing**: EV/OV cert (Windows), Apple Developer ID (macOS) —
   purchase decision; unsigned builds ship with warnings until then.
4. **Email sending** for verify/reset (Hub auth): which provider?
5. **Billing tiers**: the rate-limit table assumes free/pro/enterprise —
   payment processing is explicitly out of scope until the owner says so.

---

## 8. Status / الحالة

| Deliverable | Status |
|---|---|
| `docs/SAAS_ARCHITECTURE.md` (this file) | **DONE** |
| STEP 1 — `TOOL_EXECUTION` + orchestrator + tests | **DONE** |
| STEP 2 — `protocol.py` + tests | **DONE** |
| STEP 3 — `aali_hub/` full module + Docker + systemd | **DONE** (core live, in `.venv-hub`; WS gateway + auth + queue + registry + updates + audit + admin + `/v1` proxy) |
| STEP 4 — `AALI_MODE=saas` in `app.py` | PLANNED |
| STEP 5 — Aali Node app + installers | PLANNED (shell decision pending) |
| STEP 6-9 — security hardening, CI/CD, docs set | PLANNED |
| STEP 10 — distribution layer (HF/OpenRouter/OpenCode/vLLM) | PLANNED (`/v1` on brain already LIVE) |

Local mode, the live brain, and the full test suite are untouched by this
workstream: suite is **505 green** in the training venv (zero new deps
there) and the Hub subset is green in the dedicated `.venv-hub`
(`requirements-hub.txt`).
