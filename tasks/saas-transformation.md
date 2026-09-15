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
  swap is owned by a later explicit step.
- PYWEBVIEW SHELL + TRAY DONE (2026-09-15, same night — see the inbox note):
  aali_node/shell.py — pure headless-testable ShellState (Arabic RTL view
  folding, capped log) + ShellApi (js_api: snapshot JSON, open_workspace,
  quit) + run_shell (daemon thread with native_confirm=True + pywebview
  window + pystray tray). Close-to-tray only while the daemon is alive (no
  orphaned hidden window); tray Quit ends the daemon THEN destroys the
  window (webview.start returns only when every window is gone); no GUI
  backend → honest headless fallback, daemon keeps serving. Tray deps are
  LAZY imports: no pystray/Pillow = close-to-tray disabled with an honest
  UI notice, shell still runs. The daemon grew an optional CONTENT-FREE
  status dict (state/hub_url/node_id/counters/last_event — no tool names,
  paths, or content; polling-friendly, written under the GIL). Daemon CLI:
  `python -m aali_node --shell --hub … --token <JWT>`. New deps pystray +
  Pillow pinned in requirements-hub.txt, installed into .venv-hub ONLY
  (training venv untouched). Tests: 13 new (ShellState folding, ShellApi,
  real offscreen tray icon, fail-fast contracts, venv-agnostic status
  contract). Suites: 615 green (.venv), 157 green hub subset incl. live WS
  e2e. Lesson: keep the status contract venv-agnostic from day one —
  find_spec guards like the house tests, rc in (1,2) tolerance.
- NEXT DONE (2026-09-15, day session — Buffy): NODE PACKAGING SHIPPED.
  aali-node.spec + launch_aali_node.py shim + scripts/build_node.bat →
  build-desktop/dist/aali-node.exe (onefile, console entry, HWK icon +
  version resource, follows the proven Aali-Desktop pattern; PyInstaller
  lives in .venv-desktop only — training venv untouched). The exe IS the
  daemon: same argv contract as `python -m aali_node` (headless + --shell).
  PROOF: scripts/smoke_aali_node.py grew --frozen-exe — the packaged exe
  connected to a real Hub and the brain dispatch → exe sandbox → signed
  result chain wrote a real file (7/7 phases green); source-mode smoke
  still 7/7 too. First build FAILED at launch (ModuleNotFoundError:
  file_agent) despite building green — sandbox imports file_agent.protocol
  at module level and the analysis pass needs file-agent/ on pathex; fixed
  and pinned by tests/test_aali_node_packaging.py (8 tests: pathex,
  hiddenimports, excludes=requests, shim, build deps, frozen smoke mode).
  cryptography ships INSIDE the exe (production Ed25519 update path; HMAC
  fallback). Frozen --activate-update stays refused (source-deploy path).
  Remaining in STEP 5: code-signing (owner certs), VPS + real secrets
  (owner), UI direction (Lead Agent inbox questions still open).

## 2026-09-15, later still — STEP 5 UI layer (Buffy)
Shell shipped as described above. UI/UX direction from the Lead Agent was
NOT in the inbox at build time; the shell follows the product's existing
language (Arabic-first RTL, warm-black #252523 + gold) and is deliberately
thin: it renders the daemon's own status, nothing invented. If direction
arrives, the shape to change is ShellState/_PAGE only — daemon contract
untouched.


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
