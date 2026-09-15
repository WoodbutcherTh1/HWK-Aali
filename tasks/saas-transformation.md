owner: Buffy (Freebuff agent session)
status: in-progress
started: 2026-09-14
resumed: 2026-09-15 night (owner away ~5h; Lead Agent=DeepSeek/Gemma appointed — see tasks/lead-agent-inbox.md)

## Mission
Transform Aali into "Aali Cloud" — a multi-tenant SaaS code agent with
external distribution. Four layers: (0) external distribution
(OpenRouter / OpenCode / HuggingFace / vLLM / any OpenAI-compatible client),
(1) Aali Hub (auth, WebSocket gateway, queue, registry, updates, audit),
(2) Aali Brain (owner's Windows PC; SaaS mode in file-agent/app.py),
(3) Aali Node (lightweight desktop app executing tools on the user's machine).
Full mission brief was supplied by the owner in-session.

## Progress (2026-09-14)
- Deliverable 1 DONE: docs/SAAS_ARCHITECTURE.md (bilingual, honest status).
- STEP 1 DONE: TOOL_EXECUTION (12 server / 12 client / memory=both) +
  CONFIRM_REQUIRED + tool_orchestrator.py (19 tests).
- STEP 2 DONE: file_agent/protocol.py — versioned HMAC-SHA256 messages,
  HKDF session keys, replay window, backoff (24 tests).
- STEP 3 DONE (core): aali_hub/ — config, auth (JWT + stdlib fallback),
  users_db (argon2 + PBKDF2 fallback), queue (fair RR, quotas, circuit
  breaker), wol, model_registry, update_server (HMAC now / Ed25519 ready),
  audit (content-free), admin_api, openai_compat (/v1 proxy with API-key
  auth + metering), ws_gateway (per-leg signing, asyncio transport),
  main (app factory). Dockerfile.hub + docker-compose.hub.yml +
  deploy/aali-hub.service + requirements-hub.txt (.venv-hub).
  Tests: 35 core + 15 HTTP incl. a live WS hello/ping round-trip.
  Suite: 505 green in the training venv (zero new deps there);
  SaaS subset 93 green in the hub venv.
- Owner decisions received: Hub = FastAPI stack in own venv (APPROVED);
  Node shell = Buffy's call → Python + pywebview + PyInstaller (the
  Aali-Desktop pattern), daemon also runnable headless so bash/zsh/
  PowerShell/CMD all work via CLI mode; Tauri documented as future option.

## Next
- STEP 4: AALI_MODE=saas wiring in file-agent/app.py (orchestrator +
  per-user memory namespaces + /api/brain/status). No new deps.
- STEP 5: aali_node/ (sandbox.py first — security-critical), then the
  pywebview shell + confirmations + tray.

## 2026-09-15 night session scope (Buffy)
- STEP 4 (saas wiring) + STEP 5 sandbox.py + headless daemon, tests green,
  small local commits. NO push (owner must review; coordination with the
  Lead Agent happens through tasks/lead-agent-inbox.md).
- RESULT: STEP 4 was already shipped (commit 367a810) — task list was stale.
  STEP 5 core DONE: aali_node/sandbox.py + daemon.py, 41 new tests
  (sandbox jail/consent/redaction + daemon session + LIVE hub↔node WS
  roundtrip in tests/test_aali_node_live.py, hub venv).
  Suite: 562 passed / 2 skipped (.venv), 149 passed / 1 skipped (hub subset
  in .venv-hub). Redaction extracted to file_agent/redaction.py (stdlib-only)
  so the Node runs without `requests`.
- UPDATE CHANNEL DONE (2026-09-15, later that night — commit below):
  Hub surface aali_hub/update_api.py (public GET /updates/latest |
  /{version}/manifest | /{version}/download, bearer-gated; admin-only
  publish/list/retire, audited; routes exist ONLY when
  AALI_UPDATE_SIGNING_KEY is set — no dev fallback by design) + Node
  client aali_node/updater.py (CLIENT-SIDE signature + sha256
  re-verification — never trusts the Hub; strict dotted semver where
  unparsable versions never win and a signed unparsable version fails
  LOUDLY; zip-slip-safe staged unzip into <install>/staged/<version>/,
  running install untouched) + daemon opt-in --update-hub/--update-key
  (env AALI_UPDATE_HUB / AALI_NODE_UPDATE_KEY) non-fatal pre-connect
  check. cryptography installed into .venv-hub (was already pinned in
  requirements-hub.txt); Ed25519 auto-selected, /health reports
  update_sig_alg. Suites: 603 green (.venv), 100 green (hub subset).
  Deliberately NOT done: activation (swap + restart) — staging only, the
  swap is owned by a later explicit step. Next: pywebview shell + tray
  (Lead Agent's UI/UX direction via tasks/lead-agent-inbox.md).
- VPS provisioning + Cloudflare Tunnel + real secrets (owner).

## Lessons (for the next agent)
- PEP 563 + FastAPI: annotations resolve against MODULE globals — never
  import WebSocket inside a factory function or define Pydantic body
  models in-function (query-param degradation, unresolvable ForwardRefs).
  aali_hub now imports FastAPI names at module level (lazy try/except)
  and takes dict bodies with manual validation in admin_api.
- Dead-brain guard: all hub HTTP tests point AALI_BRAIN_URL at port 1 so
  no test can ever reach the owner's real brain on :5055.
- Hub venv intentionally lacks Flask/requests: pre-existing brain tests
  don't collect there (by design — run the full suite in .venv, the SaaS
  subset in .venv-hub).

## Not started (needs owner decisions or later steps)
- STEP 3 Hub: new FastAPI service + new dependencies -> needs a separate
  venv and explicit owner go-ahead (no new heavy deps in the training venv).
- STEP 5 Node: Tauri vs Electron decision; Rust toolchain availability.
- Code-signing certificates (EV/OV/Apple), Hub VPS availability,
  Wake-on-LAN-capable NIC/BIOS, HuggingFace/OpenRouter accounts.

## Coordination notes
- No overlap with phase-c-own-brain-sft-relaunch (run 6 finished, archived)
  or v5-graduation-run (done).
- Live brain untouched: no changes to model/scratch/final.pt, promoted
  checkpoints, or soup.yaml. Local mode (AALI_MODE=local) stays the default
  and unchanged.
